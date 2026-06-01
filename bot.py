
import asyncio
import logging
import os
 
import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
 
BOT_TOKEN    = os.environ["BOT_TOKEN"]
SOURCE_GROUP = int(os.environ["SOURCE_GROUP_ID"])
DEST_CHANNEL = int(os.environ["DEST_CHANNEL_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]
 
URL_PAYMENTS  = "https://t.me/PlanetaOplat"
URL_EDUCATION = "https://t.me/PlanetaInformazii"
 
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
pool: asyncpg.Pool = None
 
 
async def init_db():
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            user_id        BIGINT PRIMARY KEY,
            channel_msg_id BIGINT NOT NULL,
            original_text  TEXT,
            username       TEXT,
            is_open        BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
 
async def get_task(user_id: int):
    return await pool.fetchrow(
        "SELECT channel_msg_id, is_open, original_text, username FROM tasks WHERE user_id=$1",
        user_id
    )
 
async def save_task(user_id: int, channel_msg_id: int, original_text: str, username: str):
    await pool.execute("""
        INSERT INTO tasks (user_id, channel_msg_id, original_text, username, is_open)
        VALUES ($1, $2, $3, $4, TRUE)
        ON CONFLICT (user_id) DO UPDATE
            SET channel_msg_id=EXCLUDED.channel_msg_id,
                original_text=EXCLUDED.original_text,
                username=EXCLUDED.username,
                is_open=TRUE
    """, user_id, channel_msg_id, original_text, username)
 
async def close_task(user_id: int):
    await pool.execute(
        "UPDATE tasks SET is_open=FALSE WHERE user_id=$1",
        user_id
    )
 
 
def make_keyboard(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📚 Обучение", url=URL_EDUCATION),
            InlineKeyboardButton(text="💰 Выплаты",  url=URL_PAYMENTS),
        ],
        [
            InlineKeyboardButton(text="✅ Взять задание", url=f"https://t.me/{username}"),
        ],
    ])
 
def make_closed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📚 Обучение", url=URL_EDUCATION),
            InlineKeyboardButton(text="💰 Выплаты",  url=URL_PAYMENTS),
        ],
        [
            InlineKeyboardButton(text="🔒 Задание закрыто", callback_data="noop"),
        ],
    ])
 
def build_text(original: str | None, username: str) -> str:
    header = f"🌍 <a href='https://t.me/{username}'>Планета отзывов</a>\n\n<b>НОВОЕ ЗАДАНИЕ</b>\n\n"
    return header + (original or "")
 
def build_closed_text(original: str | None, username: str) -> str:
    header = f"🌍 <a href='https://t.me/{username}'>Планета отзывов</a>\n\n<b>🔒 ЗАДАНИЕ ЗАКРЫТО</b>\n\n"
    return header + (original or "")
 
 
@dp.message(Command("close"))
async def close_handler(message: Message) -> None:
    if message.chat.id != SOURCE_GROUP:
        return
 
    user_id = message.from_user.id
    task = await get_task(user_id)
 
    if not task:
        await message.reply("У тебя нет активных заданий.")
        return
 
    if not task["is_open"]:
        await message.reply("Задание уже закрыто.")
        return
 
    channel_msg_id = task["channel_msg_id"]
    original_text  = task["original_text"]
    username       = task["username"]
 
    try:
        await bot.edit_message_text(
            chat_id=DEST_CHANNEL,
            message_id=channel_msg_id,
            text=build_closed_text(original_text, username),
            parse_mode=ParseMode.HTML,
            reply_markup=make_closed_keyboard(),
        )
    except Exception as exc:
        logger.error("Не удалось обновить сообщение в канале: %s", exc)
 
    await close_task(user_id)
    await message.reply("✅ Задание закрыто. Теперь можешь создать новое.")
 
 
@dp.message()
async def forward_handler(message: Message) -> None:
    logger.info("Получено сообщение от chat_id=%s, ожидается SOURCE_GROUP=%s", message.chat.id, SOURCE_GROUP)
 
    if message.chat.id != SOURCE_GROUP:
        logger.warning("Игнорируем: chat_id=%s не совпадает с SOURCE_GROUP=%s", message.chat.id, SOURCE_GROUP)
        return
    if not message.from_user:
        return
 
    user_id  = message.from_user.id
    username = message.from_user.username or str(user_id)
 
    task = await get_task(user_id)
    if task and task["is_open"]:
        await message.reply(
            "⛔ У тебя уже есть открытое задание.\n"
            "Закрой его командой /close и создай новое."
        )
        return
 
    original_text = message.text or message.caption or ""
    markup = make_keyboard(username)
 
    try:
        if message.text:
            sent = await bot.send_message(
                chat_id=DEST_CHANNEL,
                text=build_text(message.text, username),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        elif message.photo:
            sent = await bot.send_photo(
                chat_id=DEST_CHANNEL,
                photo=message.photo[-1].file_id,
                caption=build_text(message.caption, username),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        elif message.video:
            sent = await bot.send_video(
                chat_id=DEST_CHANNEL,
                video=message.video.file_id,
                caption=build_text(message.caption, username),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        elif message.document:
            sent = await bot.send_document(
                chat_id=DEST_CHANNEL,
                document=message.document.file_id,
                caption=build_text(message.caption, username),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        else:
            sent = await bot.forward_message(
                chat_id=DEST_CHANNEL,
                from_chat_id=SOURCE_GROUP,
                message_id=message.message_id,
            )
 
        await save_task(user_id, sent.message_id, original_text, username)
        logger.info("Задание от @%s опубликовано, msg_id=%s", username, sent.message_id)
 
    except Exception as exc:
        logger.error("Ошибка при копировании id=%s: %s", message.message_id, exc)
 
 
async def main() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    await init_db()
    logger.info("Бот запущен. Группа %s → канал %s", SOURCE_GROUP, DEST_CHANNEL)
    await dp.start_polling(bot)
 
 
if __name__ == "__main__":
    asyncio.run(main())
