"""Google Sheets sync — multi-tenant.

Architecture
============

Main spreadsheet (SPREADSHEET_ID) contains, in addition to default
vocabulary / verbs tabs, an internal sheet `_students` describing each
student's source and which lessons are unlocked:

    | telegram_id | name | spreadsheet_id | active_lessons |
    | 555111222   | Аня  | 1A2bCd…         | 1,2            |
    | 777888999   | Иван | 9Z8yXw…         | *              |

* `spreadsheet_id` empty → read from the main spreadsheet
* `active_lessons` — comma-separated values that must match the `lesson`
  column. `*` means "everything available".

The student's personal spreadsheet has the same structure as main:
sheets `vocabulary` / `Словарь` and `irregular_verbs` / `Глаголы`.

Behaviour
=========

If `_students` doesn't exist yet — the bot creates it with a header on
first sync and falls back to single-tenant mode (everyone sees the main
spreadsheet's content). The moment teacher adds any rows there → only
those telegram_ids get content (others see "загляни попозже").
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

import gspread
from google.oauth2.service_account import Credentials

from ..config import Config
from ..storage import db
from ..storage.models import VerbItem, VocabularyItem

logger = logging.getLogger(__name__)

# Full scope — sync reads + snapshot writes back
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Sheet name detection ───────────────────────────────────────────────────
VOCAB_SHEET_NAMES = ("vocabulary", "словарь", "words", "слова")
VERB_SHEET_NAMES  = ("irregular_verbs", "глаголы", "verbs", "verb",
                     "irregular")
SKIP_SHEET_KEYWORDS = (
    "произношение", "pronunciation", "progress tracker",
    "progress", "traps", "rules", "phonetics",
)
SYSTEM_SHEET_PREFIX = "_"  # snapshot / config sheets

STUDENTS_SHEET = "_students"
STUDENTS_HEADER = ["telegram_id", "name", "spreadsheet_id", "active_lessons"]

DEFAULT_ACCENTS = [
    "#2F7D5B", "#2F6FD6", "#7A4FB3", "#C44B6A", "#D97A2A", "#1A7A8A",
]


# ── creds ─────────────────────────────────────────────────────────────────

def _creds(cfg: Config) -> Credentials:
    raw = cfg.google_credentials_json
    if raw:
        raw = raw.strip()
        if raw.startswith("{"):
            return Credentials.from_service_account_info(
                json.loads(raw), scopes=SCOPES,
            )
        if os.path.isfile(raw):
            return Credentials.from_service_account_file(raw, scopes=SCOPES)
    if cfg.google_credentials_file:
        return Credentials.from_service_account_file(
            cfg.google_credentials_file, scopes=SCOPES,
        )
    raise RuntimeError("No Google credentials configured")


# ── small utils ──────────────────────────────────────────────────────────

def _to_bool(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes", "да", "y")


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _pick(record: dict, *candidates: str) -> str:
    lower_map = {_norm(k): k for k in record.keys()}
    for c in candidates:
        if _norm(c) in lower_map:
            v = record[lower_map[_norm(c)]]
            return str(v).strip() if v not in (None, "") else ""
    return ""


def _stable_id(prefix: str, *parts: str) -> str:
    key = "|".join(p.lower().strip() for p in parts if p)
    return f"{prefix}_{hashlib.md5(key.encode()).hexdigest()[:10]}"


# ── parsing ──────────────────────────────────────────────────────────────

def _parse_vocabulary(records: list[dict]) -> list[VocabularyItem]:
    items: list[VocabularyItem] = []
    seen: set[str] = set()
    for i, r in enumerate(records):
        word = _pick(r, "word", "слово", "english")
        translation = _pick(r, "translation", "перевод", "russian", "значение")
        if not word or not translation:
            continue
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        wid = _pick(r, "id") or _stable_id("v", word)
        accent = _pick(r, "accent") or DEFAULT_ACCENTS[i % len(DEFAULT_ACCENTS)]
        active_raw = _pick(r, "active")
        active = _to_bool(active_raw) if active_raw else True
        lesson_raw = _pick(r, "lesson", "урок")
        items.append(VocabularyItem(
            id=wid,
            lesson=int(lesson_raw) if lesson_raw.isdigit() else 0,
            word=word,
            transcription=_pick(r, "transcription", "транскрипция", "ipa"),
            translation=translation,
            example=_pick(r, "example", "пример", "подсказка"),
            highlight=_pick(r, "highlight") or word,
            accent=accent,
            level=_pick(r, "level", "уровень") or lesson_raw,
            active=active,
        ))
    return items


def _parse_verbs(records: list[dict]) -> list[VerbItem]:
    items: list[VerbItem] = []
    seen: set[str] = set()
    for i, r in enumerate(records):
        inf = _pick(r, "infinitive", "verb", "инфинитив", "глагол",
                    "v1", "форма 1", "1 форма")
        ps  = _pick(r, "past_simple", "past simple", "прошедшее", "v2",
                    "форма 2", "2 форма")
        pp  = _pick(r, "past_participle", "past participle", "причастие",
                    "v3", "форма 3", "3 форма")
        tr  = _pick(r, "translation", "перевод", "russian")
        if not (inf and ps and pp and tr):
            continue
        if inf.lower() in seen:
            continue
        seen.add(inf.lower())
        vid = _pick(r, "id") or _stable_id("vb", inf)
        accent = _pick(r, "accent") or DEFAULT_ACCENTS[i % len(DEFAULT_ACCENTS)]
        active_raw = _pick(r, "active")
        active = _to_bool(active_raw) if active_raw else True
        lesson_raw = _pick(r, "lesson", "урок")
        items.append(VerbItem(
            id=vid,
            lesson=int(lesson_raw) if lesson_raw.isdigit() else 0,
            infinitive=inf, past_simple=ps, past_participle=pp,
            translation=tr,
            example=_pick(r, "example", "пример"),
            highlight=_pick(r, "highlight") or ps,
            accent=accent,
            active=active,
        ))
    return items


# ── sheet classification ─────────────────────────────────────────────────

def _classify(title: str, header: list[str]) -> str:
    t = _norm(title)
    if title.startswith(SYSTEM_SHEET_PREFIX):
        return "skip"
    if any(k in t for k in SKIP_SHEET_KEYWORDS):
        return "skip"
    if t in VERB_SHEET_NAMES or any(k in t for k in ("глагол", "irregular")):
        return "verb"
    if t in VOCAB_SHEET_NAMES or any(k in t for k in ("словар", "vocab", "word")):
        return "vocab"
    headers_l = [_norm(h) for h in header]
    if any(h in ("past simple", "past_simple", "v2", "прошедшее") for h in headers_l):
        return "verb"
    if any(h in ("word", "слово", "english") for h in headers_l):
        return "vocab"
    return "skip"


# ── read a single spreadsheet (vocab + verbs) ────────────────────────────

def _read_spreadsheet(client, spreadsheet_id: str
                      ) -> tuple[list[dict], list[dict]]:
    ss = client.open_by_key(spreadsheet_id)
    vocab_records: list[dict] = []
    verb_records: list[dict] = []
    for ws in ss.worksheets():
        rows = ws.get_all_values()
        if not rows:
            continue
        header = rows[0]
        kind = _classify(ws.title, header)
        if kind == "skip":
            continue
        try:
            records = ws.get_all_records()
        except Exception as e:
            logger.warning("Sheet «%s» (%s): %s", ws.title, spreadsheet_id, e)
            continue
        if kind == "vocab":
            vocab_records.extend(records)
        else:
            verb_records.extend(records)
    return vocab_records, verb_records


# ── _students sheet handling ─────────────────────────────────────────────

def _get_or_create_students_ws(ss):
    try:
        return ss.worksheet(STUDENTS_SHEET)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=STUDENTS_SHEET, rows=200, cols=4)
        ws.update("A1", [STUDENTS_HEADER])
        ws.format("A1:D1", {"textFormat": {"bold": True}})
        logger.info("Created %s sheet", STUDENTS_SHEET)
        return ws


def _read_students_rows(ws) -> list[dict]:
    try:
        records = ws.get_all_records(expected_headers=STUDENTS_HEADER)
    except Exception:
        records = ws.get_all_records()
    rows: list[dict] = []
    for r in records:
        tid = str(r.get("telegram_id") or "").strip()
        if not tid.isdigit():
            continue
        rows.append({
            "telegram_id": int(tid),
            "name": str(r.get("name") or "").strip(),
            "spreadsheet_id": str(r.get("spreadsheet_id") or "").strip(),
            "active_lessons": str(r.get("active_lessons") or "").strip() or "*",
        })
    return rows


def _ensure_students_sheet(ss, known_users: list[tuple]) -> list[dict]:
    """Returns the up-to-date `_students` rows.

    - Creates the sheet if missing
    - Auto-appends any registered users (from DB) not yet present, with
      empty spreadsheet_id and active_lessons='*' (so they get default
      content out of the box)
    - Never modifies rows the teacher has already edited
    """
    ws = _get_or_create_students_ws(ss)
    rows = _read_students_rows(ws)
    existing = {r["telegram_id"] for r in rows}

    new_rows = []
    for u in known_users:
        tid, username, first_name, _ = u
        if tid in existing:
            continue
        display = first_name or (f"@{username}" if username else f"id {tid}")
        new_rows.append([tid, display, "", "*"])
        rows.append({
            "telegram_id": tid, "name": display,
            "spreadsheet_id": "", "active_lessons": "*",
        })
        existing.add(tid)

    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
        logger.info("Auto-added %d new student(s) to %s",
                    len(new_rows), STUDENTS_SHEET)
    return rows


def append_student_row(cfg: Config, telegram_id: int,
                        name: str) -> None:
    """Best-effort immediate append. Called right when a brand-new user
    starts the bot, so the row appears within seconds (not after the
    next 30-min sync)."""
    try:
        client = gspread.authorize(_creds(cfg))
        ss = client.open_by_key(cfg.spreadsheet_id)
        ws = _get_or_create_students_ws(ss)
        existing = {r["telegram_id"] for r in _read_students_rows(ws)}
        if telegram_id in existing:
            return
        ws.append_row([telegram_id, name, "", "*"],
                      value_input_option="RAW")
        logger.info("New student %s (%s) added to %s",
                    telegram_id, name, STUDENTS_SHEET)
    except Exception as e:
        logger.warning("Failed to add student %s to %s: %s",
                       telegram_id, STUDENTS_SHEET, e)


def _lessons_match(item_lesson, item_level: str, active: str) -> bool:
    """Decide if item with given lesson/level passes the active filter."""
    active = active.strip()
    if not active or active == "*":
        return True
    tokens = [t.strip().lower() for t in active.split(",") if t.strip()]
    candidates = {str(item_lesson).strip().lower(),
                  str(item_level or "").strip().lower()}
    return any(t in candidates for t in tokens)


# ── orchestrator (blocking) ──────────────────────────────────────────────

def _sync_blocking(cfg: Config, known_users: list[tuple]) -> dict:
    """Returns summary stats. Heavy work — runs in a thread."""
    client = gspread.authorize(_creds(cfg))
    main_ss = client.open_by_key(cfg.spreadsheet_id)

    student_rows = _ensure_students_sheet(main_ss, known_users)

    # Always also read the main spreadsheet (default content pool)
    main_vocab, main_verbs = _read_spreadsheet(client, cfg.spreadsheet_id)

    # Cache per-spreadsheet reads so two students sharing a source only
    # cost one API round-trip
    source_cache: dict[str, tuple[list[dict], list[dict]]] = {
        cfg.spreadsheet_id: (main_vocab, main_verbs),
    }

    all_vocab: dict[str, VocabularyItem] = {}
    all_verbs: dict[str, VerbItem] = {}

    # Always upsert main content (so single-tenant mode still works)
    for it in _parse_vocabulary(main_vocab):
        all_vocab[it.id] = it
    for it in _parse_verbs(main_verbs):
        all_verbs[it.id] = it

    # Per-student assignments
    student_assignments: dict[int, dict[str, list[str]]] = {}

    for s in student_rows:
        source_id = s["spreadsheet_id"] or cfg.spreadsheet_id
        if source_id not in source_cache:
            try:
                source_cache[source_id] = _read_spreadsheet(client, source_id)
            except Exception as e:
                logger.warning("Cannot open spreadsheet %s for %s: %s",
                               source_id, s.get("name"), e)
                continue
        v_recs, vb_recs = source_cache[source_id]
        v_items = _parse_vocabulary(v_recs)
        vb_items = _parse_verbs(vb_recs)

        # filter by active_lessons
        active = s["active_lessons"]
        vocab_ids = [
            it.id for it in v_items
            if _lessons_match(it.lesson, it.level, active)
        ]
        verb_ids = [
            it.id for it in vb_items
            if _lessons_match(it.lesson, "", active)
        ]
        for it in v_items:
            all_vocab[it.id] = it
        for it in vb_items:
            all_verbs[it.id] = it
        student_assignments[s["telegram_id"]] = {
            "word": vocab_ids,
            "verb": verb_ids,
        }

    return {
        "students": student_rows,
        "vocab_items": list(all_vocab.values()),
        "verb_items":  list(all_verbs.values()),
        "assignments": student_assignments,
    }


async def sync_from_sheets(cfg: Config) -> tuple[int, int]:
    loop = asyncio.get_running_loop()
    known_users = await db.dump_users()
    result = await loop.run_in_executor(
        None, _sync_blocking, cfg, known_users
    )

    # Upsert all collected content (deduplicated by id)
    nv = await db.upsert_vocabulary(result["vocab_items"])
    nb = await db.upsert_verbs(result["verb_items"])

    # Mirror _students into SQLite + replace assignments
    await db.replace_students_config(result["students"])
    for tg, mapping in result["assignments"].items():
        await db.replace_student_assignments(tg, "word", mapping["word"])
        await db.replace_student_assignments(tg, "verb", mapping["verb"])

    logger.info(
        "Sync: %d vocab, %d verbs, %d students, "
        "assignments: %s",
        nv, nb, len(result["students"]),
        {tg: (len(m["word"]), len(m["verb"]))
         for tg, m in result["assignments"].items()},
    )
    return nv, nb
