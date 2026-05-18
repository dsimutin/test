"""Google Sheets sync. Tolerant to sheet/column naming — works with both
the canonical schema (`vocabulary` / `irregular_verbs` with English columns)
and the legacy Russian schema (`Словарь` / `Глаголы`).

Skips system sheets (Pronunciation, Progress Tracker, etc.).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re

import gspread
from google.oauth2.service_account import Credentials

from ..config import Config
from ..storage import db
from ..storage.models import VerbItem, VocabularyItem

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Sheet name detection ───────────────────────────────────────────────────
VOCAB_SHEET_NAMES = ("vocabulary", "словарь", "words", "слова")
VERB_SHEET_NAMES  = ("irregular_verbs", "глаголы", "verbs", "verb",
                     "irregular")
SKIP_SHEET_KEYWORDS = (
    "произношение", "pronunciation", "progress tracker",
    "progress", "traps", "rules", "phonetics",
)

# Default accent palette cycled by index when sheet has no `accent` column
DEFAULT_ACCENTS = [
    "#2F7D5B", "#2F6FD6", "#7A4FB3", "#C44B6A", "#D97A2A", "#1A7A8A",
]


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


def _to_bool(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes", "да", "y")


def _norm(s: str) -> str:
    return str(s or "").strip().lower()


def _pick(record: dict, *candidates: str) -> str:
    """Case-insensitive lookup of the first matching column."""
    lower_map = { _norm(k): k for k in record.keys() }
    for c in candidates:
        if _norm(c) in lower_map:
            v = record[lower_map[_norm(c)]]
            return str(v).strip() if v not in (None, "") else ""
    return ""


def _stable_id(prefix: str, *parts: str) -> str:
    """Build a stable id from text parts (so re-syncing the same row keeps id)."""
    key = "|".join(p.lower().strip() for p in parts if p)
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


# ── parsing ───────────────────────────────────────────────────────────────

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

        explicit_id = _pick(r, "id")
        wid = explicit_id or _stable_id("v", word)

        accent = _pick(r, "accent") or DEFAULT_ACCENTS[i % len(DEFAULT_ACCENTS)]
        active_raw = _pick(r, "active")
        active = _to_bool(active_raw) if active_raw else True

        items.append(VocabularyItem(
            id=wid,
            lesson=int(_pick(r, "lesson") or 0) if _pick(r, "lesson").isdigit() else 0,
            word=word,
            transcription=_pick(r, "transcription", "транскрипция", "ipa"),
            translation=translation,
            example=_pick(r, "example", "пример", "подсказка"),
            highlight=_pick(r, "highlight") or word,
            accent=accent,
            level=_pick(r, "level", "уровень"),
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

        explicit_id = _pick(r, "id")
        vid = explicit_id or _stable_id("vb", inf)

        accent = _pick(r, "accent") or DEFAULT_ACCENTS[i % len(DEFAULT_ACCENTS)]
        active_raw = _pick(r, "active")
        active = _to_bool(active_raw) if active_raw else True

        items.append(VerbItem(
            id=vid,
            lesson=int(_pick(r, "lesson") or 0) if _pick(r, "lesson").isdigit() else 0,
            infinitive=inf,
            past_simple=ps,
            past_participle=pp,
            translation=tr,
            example=_pick(r, "example", "пример"),
            highlight=_pick(r, "highlight") or ps,
            accent=accent,
            active=active,
        ))
    return items


# ── sheet classification ──────────────────────────────────────────────────

def _classify(title: str, header: list[str]) -> str:
    """Returns 'vocab' | 'verb' | 'skip'."""
    t = _norm(title)
    if any(k in t for k in SKIP_SHEET_KEYWORDS):
        return "skip"
    if t in VERB_SHEET_NAMES or any(k in t for k in ("глагол", "irregular")):
        return "verb"
    if t in VOCAB_SHEET_NAMES or any(k in t for k in ("словар", "vocab", "word")):
        return "vocab"

    # Detect by columns
    headers_l = [_norm(h) for h in header]
    if any(h in ("past simple", "past_simple", "v2", "прошедшее") for h in headers_l):
        return "verb"
    if any(h in ("word", "слово", "english") for h in headers_l):
        return "vocab"
    return "skip"


# ── blocking sheet read ───────────────────────────────────────────────────

def _read_blocking(cfg: Config) -> tuple[list[dict], list[dict]]:
    client = gspread.authorize(_creds(cfg))
    ss = client.open_by_key(cfg.spreadsheet_id)

    vocab_records: list[dict] = []
    verb_records:  list[dict] = []

    for ws in ss.worksheets():
        rows = ws.get_all_values()
        if not rows:
            continue
        header = rows[0]
        kind = _classify(ws.title, header)
        if kind == "skip":
            logger.info("Skipping sheet «%s»", ws.title)
            continue
        try:
            records = ws.get_all_records()
        except Exception as e:
            logger.warning("Sheet «%s»: get_all_records failed (%s)", ws.title, e)
            continue
        logger.info("Sheet «%s» → %s, %d rows", ws.title, kind, len(records))
        if kind == "vocab":
            vocab_records.extend(records)
        else:
            verb_records.extend(records)

    return vocab_records, verb_records


async def sync_from_sheets(cfg: Config) -> tuple[int, int]:
    loop = asyncio.get_running_loop()
    vocab_records, verb_records = await loop.run_in_executor(
        None, _read_blocking, cfg
    )
    vocab = _parse_vocabulary(vocab_records)
    verbs = _parse_verbs(verb_records)
    nv = await db.upsert_vocabulary(vocab)
    nb = await db.upsert_verbs(verbs)
    logger.info("Sync upserted: vocab=%d verbs=%d", nv, nb)
    return nv, nb
