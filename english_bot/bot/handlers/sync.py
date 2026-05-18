from aiogram import F, Router, types

from ..config import Config
from ..keyboards.reply import BTN_SYNC
from ..services.sheets import sync_from_sheets

router = Router(name="sync")


@router.message(F.text == BTN_SYNC)
async def on_sync(msg: types.Message, cfg: Config) -> None:
    await msg.answer("⏳ Синхронизирую с Google Sheets…")
    try:
        nv, nb = await sync_from_sheets(cfg)
        await msg.answer(
            f"✅ Готово.\nСлова: <b>{nv}</b>\nГлаголы: <b>{nb}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка синхронизации: <code>{e}</code>",
                          parse_mode="HTML")
