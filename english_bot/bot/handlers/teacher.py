"""Teacher-only commands:
   /students            — list everyone with summary stats
   /student <telegram>  — detailed view for one student
   /myid                — show the caller's telegram_id (handy for setup)

Access controlled by TEACHER_IDS env var (comma-separated telegram ids).
"""
import html

from aiogram import Router, types
from aiogram.filters import Command

from ..config import Config
from ..storage import db

router = Router(name="teacher")


@router.message(Command("myid"))
async def cmd_myid(msg: types.Message) -> None:
    await msg.answer(
        f"Твой Telegram ID: <code>{msg.from_user.id}</code>\n\n"
        f"Чтобы стать преподавателем — добавь его в переменную "
        f"<code>TEACHER_IDS</code> в Railway."
    )


@router.message(Command("students"))
async def cmd_students(msg: types.Message, cfg: Config) -> None:
    if not cfg.is_teacher(msg.from_user.id):
        await msg.answer("⛔ Команда только для преподавателя.")
        return

    rows = await db.list_all_students()
    if not rows:
        await msg.answer("Пока нет учеников.")
        return

    lines = ["👥 <b>Ученики</b>\n"]
    for i, r in enumerate(rows, 1):
        name = html.escape(r["first_name"] or "—")
        uname = f"@{r['username']}" if r["username"] else "—"
        last = r["last_session"][:10] if r["last_session"] else "—"
        lines.append(
            f"{i}. <b>{name}</b>  {uname}\n"
            f"   ID: <code>{r['telegram_id']}</code>\n"
            f"   📚 выучено {r['words_known']}, повтор {r['words_review']}\n"
            f"   ⚡ выучено {r['verbs_known']}, повтор {r['verbs_review']}\n"
            f"   Последняя сессия: {last}\n"
        )
    lines.append(
        "\nДетально: <code>/student &lt;ID&gt;</code>"
    )
    await msg.answer("\n".join(lines))


@router.message(Command("student"))
async def cmd_student(msg: types.Message, cfg: Config) -> None:
    if not cfg.is_teacher(msg.from_user.id):
        await msg.answer("⛔ Команда только для преподавателя.")
        return

    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer(
            "Использование: <code>/student 123456789</code>\n"
            "ID можно взять из <code>/students</code>."
        )
        return

    student_id = int(parts[1])
    data = await db.student_detail(student_id)
    if data is None:
        await msg.answer("Ученик не найден.")
        return

    name = html.escape(data["first_name"] or "—")
    uname = f"@{data['username']}" if data["username"] else "—"
    created = data["created_at"][:10] if data["created_at"] else "—"

    lines = [
        f"👤 <b>{name}</b>  {uname}",
        f"ID: <code>{student_id}</code>",
        f"С нами с: {created}\n",
    ]

    if data["sessions"]:
        lines.append("<b>Последние сессии:</b>")
        for s in data["sessions"]:
            when = (s["finished_at"] or s["started_at"] or "")[:16].replace("T", " ")
            mode = "📚 слова" if s["mode"] == "vocab" else "⚡ глаголы"
            if s["finished_at"]:
                lines.append(
                    f"  • {when} — {mode}: "
                    f"{s['correct']}/{s['total']} ✓, {s['wrong']} ✗"
                )
            else:
                lines.append(f"  • {when} — {mode}: не завершена")
        lines.append("")

    if data["worst_words"]:
        lines.append("<b>Слова с ошибками:</b>")
        for w in data["worst_words"]:
            lines.append(
                f"  • {html.escape(w['word'])} — "
                f"{html.escape(w['translation'])}  "
                f"<i>(❌{w['wrong']} / ✓{w['correct']})</i>"
            )
        lines.append("")

    if data["worst_verbs"]:
        lines.append("<b>Глаголы с ошибками:</b>")
        for v in data["worst_verbs"]:
            extra = []
            if v["ps_errors"]:
                extra.append(f"PS:{v['ps_errors']}")
            if v["pp_errors"]:
                extra.append(f"PP:{v['pp_errors']}")
            tag = (" [" + ", ".join(extra) + "]") if extra else ""
            lines.append(
                f"  • {html.escape(v['infinitive'])} — "
                f"{html.escape(v['translation'])}  "
                f"<i>(❌{v['wrong']} / ✓{v['correct']}){tag}</i>"
            )

    if not data["sessions"] and not data["worst_words"] and not data["worst_verbs"]:
        lines.append("<i>Пока нет активности.</i>")

    await msg.answer("\n".join(lines))
