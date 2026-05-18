from aiogram import F, Router, types

from ..keyboards.reply import BTN_STATS
from ..storage import db

router = Router(name="stats")


@router.message(F.text == BTN_STATS)
async def on_stats(msg: types.Message) -> None:
    uid = msg.from_user.id
    v = await db.vocab_stats(uid)
    g = await db.verb_stats(uid)
    last = await db.last_session_date(uid)
    last_str = last[:10] if last else "—"

    text = (
        "📊 <b>Твоя статистика</b>\n\n"
        "📚 <b>Слова</b>\n"
        f"  Выучено: <b>{v['known']}</b>\n"
        f"  На повторении: <b>{v['review']}</b>\n"
        f"  Новых осталось: <b>{v['new']}</b>\n\n"
        "⚡ <b>Глаголы</b>\n"
        f"  Выучено: <b>{g['known']}</b>\n"
        f"  На повторении: <b>{g['review']}</b>\n"
        f"  Новых осталось: <b>{g['new']}</b>\n\n"
        f"<i>Последняя тренировка: {last_str}</i>"
    )
    await msg.answer(text, parse_mode="HTML")
