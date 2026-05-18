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
STUDENTS_HEADER = ["telegram_id", "name", "words_tabs", "verbs_tabs"]

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


def _split_tabs(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


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
            "words_tabs": _split_tabs(str(r.get("words_tabs") or "")),
            "verbs_tabs": _split_tabs(str(r.get("verbs_tabs") or "")),
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
        new_rows.append([tid, display, "", ""])
        rows.append({
            "telegram_id": tid, "name": display,
            "words_tabs": [], "verbs_tabs": [],
        })
        existing.add(tid)

    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
        logger.info("Auto-added %d new student(s) to %s",
                    len(new_rows), STUDENTS_SHEET)
    return rows


def append_student_row(cfg: Config, telegram_id: int,
                        name: str) -> None:
    """Idempotent: appends a row to _students iff the telegram_id isn't
    already present. Called from middleware on first interaction."""
    client = gspread.authorize(_creds(cfg))
    ss = client.open_by_key(cfg.spreadsheet_id)
    ws = _get_or_create_students_ws(ss)
    existing = {r["telegram_id"] for r in _read_students_rows(ws)}
    if telegram_id in existing:
        logger.info("Student %s already in %s — skipping",
                    telegram_id, STUDENTS_SHEET)
        return
    ws.append_row([telegram_id, name, "", ""], value_input_option="RAW")
    logger.info("✅ Added student %s (%s) to %s",
                telegram_id, name, STUDENTS_SHEET)


# ── orchestrator (blocking) ──────────────────────────────────────────────

def _read_tab_records(ws_by_title: dict, tab_name: str) -> list[dict]:
    ws = ws_by_title.get(_norm(tab_name))
    if ws is None:
        logger.warning("Tab «%s» not found", tab_name)
        return []
    try:
        return ws.get_all_records()
    except Exception as e:
        logger.warning("Tab «%s» read error: %s", tab_name, e)
        return []


def _default_vocab_tabs(ws_by_title: dict) -> list[str]:
    """Return the first matching default vocab tab title."""
    for cand in VOCAB_SHEET_NAMES:
        if cand in ws_by_title:
            return [ws_by_title[cand].title]
    # fall back: any tab classified as vocab (excluding system sheets)
    for ws in ws_by_title.values():
        rows = ws.get_all_values()
        header = rows[0] if rows else []
        if _classify(ws.title, header) == "vocab":
            return [ws.title]
    return []


def _default_verb_tabs(ws_by_title: dict) -> list[str]:
    for cand in VERB_SHEET_NAMES:
        if cand in ws_by_title:
            return [ws_by_title[cand].title]
    for ws in ws_by_title.values():
        rows = ws.get_all_values()
        header = rows[0] if rows else []
        if _classify(ws.title, header) == "verb":
            return [ws.title]
    return []


def _sync_blocking(cfg: Config, known_users: list[tuple]) -> dict:
    """Returns summary stats. Heavy work — runs in a thread."""
    client = gspread.authorize(_creds(cfg))
    main_ss = client.open_by_key(cfg.spreadsheet_id)

    student_rows = _ensure_students_sheet(main_ss, known_users)

    # Build tab index: lower-cased title → worksheet
    ws_by_title = {_norm(ws.title): ws for ws in main_ss.worksheets()}

    # Detect default tabs (used when student row has empty words_tabs / verbs_tabs)
    default_vocab_tabs = _default_vocab_tabs(ws_by_title)
    default_verb_tabs  = _default_verb_tabs(ws_by_title)
    logger.info("Default vocab tabs: %s, verb tabs: %s",
                default_vocab_tabs, default_verb_tabs)

    # Cache per-tab parsed content (one parse per tab, even if 10 students share it)
    vocab_cache: dict[str, list[VocabularyItem]] = {}
    verb_cache:  dict[str, list[VerbItem]] = {}

    def _vocab_for(tab: str) -> list[VocabularyItem]:
        key = _norm(tab)
        if key not in vocab_cache:
            vocab_cache[key] = _parse_vocabulary(
                _read_tab_records(ws_by_title, tab)
            )
        return vocab_cache[key]

    def _verbs_for(tab: str) -> list[VerbItem]:
        key = _norm(tab)
        if key not in verb_cache:
            verb_cache[key] = _parse_verbs(
                _read_tab_records(ws_by_title, tab)
            )
        return verb_cache[key]

    all_vocab: dict[str, VocabularyItem] = {}
    all_verbs: dict[str, VerbItem] = {}

    # Always preload defaults (so single-tenant fallback and brand-new
    # students who haven't run sync yet still have content)
    for tab in default_vocab_tabs:
        for it in _vocab_for(tab):
            all_vocab[it.id] = it
    for tab in default_verb_tabs:
        for it in _verbs_for(tab):
            all_verbs[it.id] = it

    student_assignments: dict[int, dict[str, list[str]]] = {}

    for s in student_rows:
        w_tabs = s["words_tabs"] or default_vocab_tabs
        v_tabs = s["verbs_tabs"] or default_verb_tabs

        word_ids: list[str] = []
        for tab in w_tabs:
            items = _vocab_for(tab)
            for it in items:
                all_vocab[it.id] = it
            word_ids.extend(it.id for it in items)

        verb_ids: list[str] = []
        for tab in v_tabs:
            items = _verbs_for(tab)
            for it in items:
                all_verbs[it.id] = it
            verb_ids.extend(it.id for it in items)

        # de-dup preserving order
        student_assignments[s["telegram_id"]] = {
            "word": list(dict.fromkeys(word_ids)),
            "verb": list(dict.fromkeys(verb_ids)),
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
