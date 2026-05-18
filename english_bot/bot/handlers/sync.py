from aiogram import F, Router, types

from ..config import Config
from ..keyboards.reply import BTN_SYNC
from ..services.sheets import sync_from_sheets

router = Router(name="sync")


@router.message(F.text == BTN_SYNC)
async def on_sync(msg: types.Message, cfg: Config) -> None:
    if not cfg.is_teacher(msg.from_user.id, msg.from_user.username):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    await msg.answer("⏳ Синхронизирую с Google Sheets…")
    try:
        nv, nb = await sync_from_sheets(cfg)
        await msg.answer(
            f"✅ Готово.\nСлова: <b>{nv}</b>\nГлаголы: <b>{nb}</b>"
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка синхронизации: <code>{e}</code>")
