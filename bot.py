import asyncio
import json
import logging
import os
import random
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

DATA_FILE = Path(__file__).parent / "data.json"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

active_chats = set()

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


async def send_entry(target, entry: dict, reply_to: Message | None = None):
    """Отправляет фразу — с фото (если прикреплено) или просто текстом.
    target — это bot.send_* получатель (chat_id) или сам message для reply.
    """
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
    active_chats.add(message.chat.id)
    data = load_data()
    chance = data["settings"]["activity_percent"] / 100
    if random.random() < chance:
        entry = random.choice(data["photo_compliments"])
        await send_entry(None, entry, reply_to=message)


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    active_chats.add(message.chat.id)
    data = load_data()
    chance = (data["settings"]["activity_percent"] / 100) * 0.3
    if random.random() < chance:
        entry = random.choice(data["random_phrases"])
        await send_entry(None, entry, reply_to=message)


# ---------- Команды управления ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    active_chats.add(message.chat.id)
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


# ---------- Запланированные сообщения ----------

async def send_morning():
    data = load_data()
    entry = random.choice(data["morning_phrases"])
    for chat_id in active_chats:
        try:
            await send_entry(chat_id, entry)
        except Exception as e:
            logger.warning(f"Не удалось отправить утреннее сообщение в {chat_id}: {e}")


async def send_night():
    data = load_data()
    entry = random.choice(data["night_phrases"])
    for chat_id in active_chats:
        try:
            await send_entry(chat_id, entry)
        except Exception as e:
            logger.warning(f"Не удалось отправить ночное сообщение в {chat_id}: {e}")


def setup_scheduler():
    data = load_data()
    tz = data["settings"]["timezone"]
    morning_h, morning_m = map(int, data["settings"]["morning_time"].split(":"))
    night_h, night_m = map(int, data["settings"]["night_time"].split(":"))

    scheduler.configure(timezone=tz)
    scheduler.add_job(send_morning, "cron", hour=morning_h, minute=morning_m)
    scheduler.add_job(send_night, "cron", hour=night_h, minute=night_m)
    scheduler.start()


async def main():
    setup_scheduler()
    logger.info("Бот запущен, жду сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
