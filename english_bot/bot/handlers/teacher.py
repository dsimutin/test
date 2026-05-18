"""Teacher-only features:
   /myid                — show caller's telegram_id (everyone)
   /students  + 👥      — interactive student list with inline drill-down
   /student <id>        — same detail view (textual entry)
   📋 Экспорт CSV       — full progress dump as CSV file

Access controlled by TEACHER_IDS / TEACHER_USERNAMES env vars.
"""
import html

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from ..config import Config
from ..keyboards.reply import BTN_BACKUP, BTN_EXPORT, BTN_STUDENTS
from ..services.exporter import build_progress_csv
from ..services.snapshot import dump_to_sheets
from ..storage import db

router = Router(name="teacher")

PAGE_SIZE = 10


def _is_teacher(msg_or_cb, cfg: Config) -> bool:
    u = msg_or_cb.from_user
    return cfg.is_teacher(u.id, u.username)


# ── /myid (everyone) ──────────────────────────────────────────────────────

@router.message(Command("myid"))
async def cmd_myid(msg: types.Message) -> None:
    uname = f"@{msg.from_user.username}" if msg.from_user.username else "—"
    await msg.answer(
        f"Твой Telegram ID: <code>{msg.from_user.id}</code>\n"
        f"Username: {uname}\n\n"
        "Чтобы стать преподавателем — добавь свой ID в "
        "<code>TEACHER_IDS</code> или username в <code>TEACHER_USERNAMES</code> "
        "(Railway → Variables)."
    )


# ── students list (reply button + slash command + inline pagination) ──────

def _students_kb(rows: list[dict], page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = rows[start:end]
    buttons = []
    for r in chunk:
        name = r["first_name"] or (f"@{r['username']}" if r["username"]
                                    else f"id {r['telegram_id']}")
        # short summary in button text
        summary = f"📚 {r['words_known']} · ⚡ {r['verbs_known']}"
        buttons.append([InlineKeyboardButton(
            text=f"{name}   {summary}",
            callback_data=f"stu:detail:{r['telegram_id']}",
        )])
    # pagination row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀", callback_data=f"stu:list:{page - 1}"))
    if end < len(rows):
        nav.append(InlineKeyboardButton(
            text="▶", callback_data=f"stu:list:{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _render_students_list(target, cfg: Config, page: int,
                                  edit: bool = False) -> None:
    rows = await db.list_all_students()
    if not rows:
        text = "Пока нет учеников. Дай им ссылку на бота!"
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return
    total_pages = (len(rows) - 1) // PAGE_SIZE + 1
    text = (
        f"👥 <b>Ученики</b> ({len(rows)})\n"
        f"Страница {page + 1}/{total_pages}\n\n"
        f"<i>Нажми на ученика для деталей.</i>"
    )
    kb = _students_kb(rows, page)
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(F.text == BTN_STUDENTS)
@router.message(Command("students"))
async def on_students(msg: types.Message, cfg: Config) -> None:
    if not _is_teacher(msg, cfg):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    await _render_students_list(msg, cfg, page=0)


@router.callback_query(F.data.startswith("stu:list:"))
async def cb_students_page(cb: types.CallbackQuery, cfg: Config) -> None:
    if not _is_teacher(cb, cfg):
        await cb.answer("⛔", show_alert=True)
        return
    page = int(cb.data.split(":")[2])
    await _render_students_list(cb.message, cfg, page=page, edit=True)
    await cb.answer()


# ── student detail (inline + slash) ───────────────────────────────────────

def _detail_kb(student_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀ К списку",
                              callback_data="stu:list:0"),
    ]])


def _format_student(data: dict) -> str:
    name = html.escape(data["first_name"] or "—")
    uname = f"@{data['username']}" if data["username"] else "—"
    created = data["created_at"][:10] if data["created_at"] else "—"

    lines = [
        f"👤 <b>{name}</b>  {uname}",
        f"ID: <code>{data['telegram_id']}</code>",
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
        lines.append("<b>📚 Слова с ошибками:</b>")
        for w in data["worst_words"]:
            lines.append(
                f"  • {html.escape(w['word'])} — "
                f"{html.escape(w['translation'])}  "
                f"<i>(❌{w['wrong']} / ✓{w['correct']})</i>"
            )
        lines.append("")

    if data["worst_verbs"]:
        lines.append("<b>⚡ Глаголы с ошибками:</b>")
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

    if not (data["sessions"] or data["worst_words"] or data["worst_verbs"]):
        lines.append("<i>Пока нет активности.</i>")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("stu:detail:"))
async def cb_student_detail(cb: types.CallbackQuery, cfg: Config) -> None:
    if not _is_teacher(cb, cfg):
        await cb.answer("⛔", show_alert=True)
        return
    student_id = int(cb.data.split(":")[2])
    data = await db.student_detail(student_id)
    if data is None:
        await cb.answer("Ученик не найден.", show_alert=True)
        return
    await cb.message.edit_text(_format_student(data),
                                reply_markup=_detail_kb(student_id))
    await cb.answer()


@router.message(Command("student"))
async def cmd_student(msg: types.Message, cfg: Config) -> None:
    if not _is_teacher(msg, cfg):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: <code>/student 123456789</code>")
        return
    data = await db.student_detail(int(parts[1]))
    if data is None:
        await msg.answer("Ученик не найден.")
        return
    await msg.answer(_format_student(data),
                      reply_markup=_detail_kb(data["telegram_id"]))


# ── CSV export ────────────────────────────────────────────────────────────

@router.message(F.text == BTN_EXPORT)
@router.message(Command("export"))
async def on_export(msg: types.Message, cfg: Config) -> None:
    if not _is_teacher(msg, cfg):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    await msg.answer("⏳ Готовлю выгрузку…")
    data, filename = await build_progress_csv()
    if not data or data == b"\xef\xbb\xbf":  # only BOM, no rows
        await msg.answer("Пока нет данных для выгрузки.")
        return
    await msg.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=(
            "📋 Полный прогресс всех учеников.\n"
            "Открой в Excel / Google Sheets (разделитель — точка с запятой)."
        ),
    )


# ── manual backup ─────────────────────────────────────────────────────────

@router.message(Command("assignments"))
async def cmd_assignments(msg: types.Message, cfg: Config) -> None:
    """Show who's set up in _students and what's assigned."""
    if not _is_teacher(msg, cfg):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    rows = await db.list_students_config()
    if not rows:
        await msg.answer(
            "Лист <code>_students</code> пуст. Как только ученик напишет "
            "боту — он там появится автоматически."
        )
        return
    lines = ["📋 <b>Назначения учеников</b>\n"]
    for r in rows:
        word_ids = await db.get_assigned_ids(r["telegram_id"], "word")
        verb_ids = await db.get_assigned_ids(r["telegram_id"], "verb")
        wt = r["words_tabs"] or "<i>дефолт</i>"
        vt = r["verbs_tabs"] or "<i>дефолт</i>"
        lines.append(
            f"<b>{html.escape(r['name'] or '—')}</b>  "
            f"<code>{r['telegram_id']}</code>\n"
            f"  📚 Слова: {html.escape(wt) if r['words_tabs'] else wt}\n"
            f"  ⚡ Глаголы: {html.escape(vt) if r['verbs_tabs'] else vt}\n"
            f"  ({len(word_ids)} слов · {len(verb_ids)} глаголов)\n"
        )
    await msg.answer("\n".join(lines))


@router.message(F.text == BTN_BACKUP)
@router.message(Command("backup"))
async def on_backup(msg: types.Message, cfg: Config) -> None:
    if not _is_teacher(msg, cfg):
        await msg.answer("⛔ Команда только для преподавателя.")
        return
    await msg.answer("⏳ Сохраняю снапшот в Google Sheets…")
    try:
        await dump_to_sheets(cfg)
        await msg.answer(
            "✅ Снапшот сохранён в листах "
            "<code>_users</code>, <code>_progress</code>, "
            "<code>_sessions</code>.\n\n"
            "<i>Автоматически делается каждые 5 минут и при перезапуске.</i>"
        )
    except Exception as e:
        await msg.answer(f"❌ Не удалось: <code>{e}</code>")
