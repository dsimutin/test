from aiogram import Router, types
from aiogram.filters import CommandStart

from ..keyboards.reply import MAIN_MENU
from ..storage import db

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(msg: types.Message) -> None:
    await db.ensure_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
    )
    await msg.answer(
        f"Привет, {msg.from_user.first_name or 'друг'}! 👋\n\n"
        "Я помогу выучить английские слова и неправильные глаголы.\n\n"
        "📚 — учим слова (по 10 за подход + проверка)\n"
        "⚡ — учим глаголы (по 5 за подход + проверка)\n"
        "📊 — твой прогресс\n"
        "🔄 — обновить базу из Google Sheets",
        reply_markup=MAIN_MENU,
    )
