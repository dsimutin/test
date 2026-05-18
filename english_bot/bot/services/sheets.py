"""Google Sheets sync. Reads two sheets:
  - 'vocabulary'
  - 'irregular_verbs'
and upserts into SQLite. Doesn't touch user progress.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Iterable

import gspread
from google.oauth2.service_account import Credentials

from ..config import Config
from ..storage import db
from ..storage.models import VerbItem, VocabularyItem

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

VOCAB_SHEET = "vocabulary"
VERBS_SHEET = "irregular_verbs"


def _creds(cfg: Config) -> Credentials:
    raw = cfg.google_credentials_json
    if raw:
        raw = raw.strip()
        # JSON pasted directly?
        if raw.startswith("{"):
            return Credentials.from_service_account_info(
                json.loads(raw), scopes=SCOPES,
            )
        # Otherwise treat as a file path (legacy var)
        if os.path.isfile(raw):
            return Credentials.from_service_account_file(raw, scopes=SCOPES)
    if cfg.google_credentials_file:
        return Credentials.from_service_account_file(
            cfg.google_credentials_file, scopes=SCOPES,
        )
    raise RuntimeError("No Google credentials configured")


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes", "да", "y")


def _parse_vocabulary(records: list[dict]) -> list[VocabularyItem]:
    items = []
    for r in records:
        wid = str(r.get("id") or "").strip()
        word = str(r.get("word") or "").strip()
        translation = str(r.get("translation") or "").strip()
        if not wid or not word or not translation:
            continue
        items.append(VocabularyItem(
            id=wid,
            lesson=int(r.get("lesson") or 0),
            word=word,
            transcription=str(r.get("transcription") or "").strip(),
            translation=translation,
            example=str(r.get("example") or "").strip(),
            highlight=str(r.get("highlight") or word).strip(),
            accent=str(r.get("accent") or "#2F7D5B").strip(),
            level=str(r.get("level") or "").strip(),
            active=_to_bool(r.get("active", "TRUE")),
        ))
    return items


def _parse_verbs(records: list[dict]) -> list[VerbItem]:
    items = []
    for r in records:
        vid = str(r.get("id") or "").strip()
        inf = str(r.get("infinitive") or "").strip()
        ps  = str(r.get("past_simple") or "").strip()
        pp  = str(r.get("past_participle") or "").strip()
        tr  = str(r.get("translation") or "").strip()
        if not (vid and inf and ps and pp and tr):
            continue
        items.append(VerbItem(
            id=vid,
            lesson=int(r.get("lesson") or 0),
            infinitive=inf,
            past_simple=ps,
            past_participle=pp,
            translation=tr,
            example=str(r.get("example") or "").strip(),
            highlight=str(r.get("highlight") or ps).strip(),
            accent=str(r.get("accent") or "#D77A22").strip(),
            active=_to_bool(r.get("active", "TRUE")),
        ))
    return items


def _sync_blocking(cfg: Config) -> tuple[int, int]:
    client = gspread.authorize(_creds(cfg))
    ss = client.open_by_key(cfg.spreadsheet_id)
    vocab_records = ss.worksheet(VOCAB_SHEET).get_all_records()
    verb_records  = ss.worksheet(VERBS_SHEET).get_all_records()
    return vocab_records, verb_records  # type: ignore[return-value]


async def sync_from_sheets(cfg: Config) -> tuple[int, int]:
    """Fetch sheets in a thread (gspread is sync), then upsert. Returns
    (vocab_count, verb_count) — number of rows upserted."""
    loop = asyncio.get_running_loop()
    vocab_records, verb_records = await loop.run_in_executor(
        None, _sync_blocking, cfg
    )
    vocab = _parse_vocabulary(vocab_records)
    verbs = _parse_verbs(verb_records)
    nv = await db.upsert_vocabulary(vocab)
    nb = await db.upsert_verbs(verbs)
    logger.info("Sync: %d vocab, %d verbs upserted", nv, nb)
    return nv, nb
