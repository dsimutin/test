from aiogram import Router, types
from aiogram.filters import CommandStart

from ..config import Config
from ..keyboards.reply import main_menu
from ..storage import db

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(msg: types.Message, cfg: Config) -> None:
    await db.ensure_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
    )
    is_teacher = cfg.is_teacher(msg.from_user.id, msg.from_user.username)

    intro = (
        f"Привет, {msg.from_user.first_name or 'друг'}! 👋\n\n"
        "Я помогу выучить английские слова и неправильные глаголы.\n\n"
        "📚 — учим слова (по 10 за подход + проверка)\n"
        "⚡ — учим глаголы (по 5 за подход + проверка)\n"
        "📊 — твой прогресс"
    )
    if is_teacher:
        intro += (
            "\n\n<b>Преподавательский режим:</b>\n"
            "👥 — список учеников и их прогресс\n"
            "🔄 — обновить базу из Google Sheets вручную\n"
            "📋 — выгрузить весь прогресс в CSV\n\n"
            "<i>База обновляется автоматически раз в 30 минут.</i>"
        )

    await msg.answer(intro, reply_markup=main_menu(is_teacher))
