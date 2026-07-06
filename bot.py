import asyncio
import json
import logging
import os
import random
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

# Полный публичный адрес вашего сервиса на Render, например:
# https://my-georgian-bot.onrender.com
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
if not WEBHOOK_HOST:
    raise RuntimeError("Не задан WEBHOOK_HOST в переменных окружения")

# Секрет для /cron/* эндпоинтов, чтобы их не мог дёргать кто попало
CRON_SECRET = os.getenv("CRON_SECRET", "change-me")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

DATA_FILE = Path(__file__).parent / "data.json"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

CATEGORY_MAP = {
    "утро": "morning_phrases",
    "ночь": "night_phrases",
    "фото": "photo_compliments",
    "цветы": "flower_phrases",
    "рандом": "random_phrases",
}


def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def remember_chat(data: dict, chat_id: int):
    """Сохраняем chat_id в data.json, чтобы список чатов не терялся,
    когда бесплатный сервис на Render засыпает и просыпается заново."""
    chats = data.setdefault("active_chats", [])
    if chat_id not in chats:
        chats.append(chat_id)
        save_data(data)


async def send_entry(target, entry: dict, reply_to: Message | None = None):
    text = entry.get("text", "")
    photo = entry.get("photo")
    if reply_to is not None:
        if photo:
            await reply_to.reply_photo(photo, caption=text)
        else:
            await reply_to.reply(text)
    else:
        chat_id = target
        if photo:
            await bot.send_photo(chat_id, photo, caption=text)
        else:
            await bot.send_message(chat_id, text)


# ---------- Хендлеры сообщений ----------

@dp.message(F.photo & ~F.caption.startswith("/"))
async def on_photo(message: Message):
    data = load_data()
    remember_chat(data, message.chat.id)
    chance = data["settings"]["activity_percent"] / 100
    if random.random() < chance:
        entry = random.choice(data["photo_compliments"])
        await send_entry(None, entry, reply_to=message)


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    data = load_data()
    remember_chat(data, message.chat.id)
    chance = (data["settings"]["activity_percent"] / 100) * 0.3
    if random.random() < chance:
        entry = random.choice(data["random_phrases"])
        await send_entry(None, entry, reply_to=message)


# ---------- Команды управления ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    data = load_data()
    remember_chat(data, message.chat.id)
    await message.answer(
        "Вах, привет! Теперь я буду с вами в этом чате 🍷\n"
        "Пишите /help чтобы посмотреть команды настройки."
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "Команды:\n"
        "/как_дела — бот пришлёт случайную тёплую фразу (иногда с фото)\n"
        "/активность 0-100 — как часто бот отвечает сам, без команд\n"
        "/добавить_фразу <категория> <текст> — добавить фразу без фото\n"
        "/список_фраз <категория> — посмотреть все фразы категории с номерами\n"
        "/тест_утро — прислать утреннее приветствие прямо сейчас\n"
        "/тест_ночь — прислать пожелание спокойной ночи прямо сейчас\n\n"
        "Категории: утро, ночь, фото, цветы, рандом\n\n"
        "Чтобы добавить фразу С ФОТО:\n"
        "1. Отправьте фото в чат\n"
        "2. В подписи к фото напишите: /добавить_фразу_с_фото <категория> <текст>"
    )
    await message.answer(text)


@dp.message(Command("активность"))
async def cmd_activity(message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("Это могут менять только админы бота.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.reply("Использование: /активность 40  (число от 0 до 100)")
        return
    value = max(0, min(100, int(parts[1])))
    data = load_data()
    data["settings"]["activity_percent"] = value
    save_data(data)
    await message.reply(f"Активность бота установлена на {value}%")


@dp.message(Command("добавить_фразу"))
async def cmd_add_phrase(message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("Это могут менять только админы бота.")
        return
    try:
        _, category, phrase = message.text.split(maxsplit=2)
    except ValueError:
        await message.reply(
            "Использование: /добавить_фразу утро Доброе утро, красавицы!\n"
            "Категории: утро, ночь, фото, цветы, рандом"
        )
        return
    key = CATEGORY_MAP.get(category.lower())
    if not key:
        await message.reply("Неизвестная категория. Доступно: утро, ночь, фото, цветы, рандом")
        return
    data = load_data()
    data[key].append({"text": phrase, "photo": None})
    save_data(data)
    await message.reply("Добавил новую фразу! 🍷")


@dp.message(F.photo & F.caption.startswith("/добавить_фразу_с_фото"))
async def cmd_add_phrase_with_photo(message: Message):
    if not is_admin(message.from_user.id):
        await message.reply("Это могут менять только админы бота.")
        return
    try:
        _, category, phrase = message.caption.split(maxsplit=2)
    except ValueError:
        await message.reply(
            "В подписи к фото напишите:\n"
            "/добавить_фразу_с_фото фото Вах, красавица!\n"
            "Категории: утро, ночь, фото, цветы, рандом"
        )
        return
    key = CATEGORY_MAP.get(category.lower())
    if not key:
        await message.reply("Неизвестная категория. Доступно: утро, ночь, фото, цветы, рандом")
        return
    file_id = message.photo[-1].file_id
    data = load_data()
    data[key].append({"text": phrase, "photo": file_id})
    save_data(data)
    await message.reply("Добавил фразу с фото! 📸")


@dp.message(Command("список_фраз"))
async def cmd_list_phrases(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply("Использование: /список_фраз утро")
        return
    key = CATEGORY_MAP.get(parts[1].lower())
    if not key:
        await message.reply("Неизвестная категория. Доступно: утро, ночь, фото, цветы, рандом")
        return
    data = load_data()
    entries = data[key]
    lines = []
    for i, e in enumerate(entries):
        mark = " 📸" if e.get("photo") else ""
        lines.append(f"{i+1}. {e['text']}{mark}")
    await message.reply("\n".join(lines) or "Пока пусто.")


@dp.message(Command("как_дела"))
async def cmd_how_are_you(message: Message):
    data = load_data()
    entry = random.choice(data["flower_phrases"])
    await send_entry(None, entry, reply_to=message)


@dp.message(Command("тест_утро"))
async def cmd_test_morning(message: Message):
    data = load_data()
    entry = random.choice(data["morning_phrases"])
    await send_entry(None, entry, reply_to=message)


@dp.message(Command("тест_ночь"))
async def cmd_test_night(message: Message):
    data = load_data()
    entry = random.choice(data["night_phrases"])
    await send_entry(None, entry, reply_to=message)


# ---------- Отправка по расписанию (вызывается внешним cron через HTTP) ----------

async def broadcast(category_key: str):
    data = load_data()
    entry = random.choice(data[category_key])
    for chat_id in data.get("active_chats", []):
        try:
            await send_entry(chat_id, entry)
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение в {chat_id}: {e}")


# ---------- Веб-сервер (webhook + эндпоинты для cron) ----------

async def handle_cron_morning(request: web.Request):
    if request.query.get("secret") != CRON_SECRET:
        return web.Response(status=403, text="forbidden")
    await broadcast("morning_phrases")
    return web.Response(text="ok")


async def handle_cron_night(request: web.Request):
    if request.query.get("secret") != CRON_SECRET:
        return web.Response(status=403, text="forbidden")
    await broadcast("night_phrases")
    return web.Response(text="ok")


async def handle_health(request: web.Request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")


def main():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/cron/morning", handle_cron_morning)
    app.router.add_get("/cron/night", handle_cron_night)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", "10000"))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
