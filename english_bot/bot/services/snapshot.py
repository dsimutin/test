"""Persist SQLite progress to Google Sheets (durability layer for envs
without a Volume — e.g. Railway free tier).

Three internal sheets, prefixed with `_` so they sit at the end of the
spreadsheet and look obviously system-owned:

    _users      — registered Telegram users
    _progress   — per-user word & verb progress (single table, `type` col)
    _sessions   — per-user training session history

On startup:    if SQLite has no per-user data, restore from these sheets.
Every 5 min:   dump current SQLite state, overwriting the sheets.

Idempotent dumps; safe to call concurrently with normal handlers (they
mutate SQLite, not the sheets).
"""
from __future__ import annotations

import asyncio
import logging

import gspread

from ..config import Config
from ..storage import db
from .sheets import _creds  # reuse credential loader

logger = logging.getLogger(__name__)

USERS_SHEET    = "_users"
PROGRESS_SHEET = "_progress"
SESSIONS_SHEET = "_sessions"

USERS_HEADER = ["telegram_id", "username", "first_name", "created_at"]
PROGRESS_HEADER = [
    "type", "telegram_id", "item_id", "status",
    "correct_count", "wrong_count",
    "ps_errors", "pp_errors",
    "last_seen_at", "next_review_at",
]
SESSIONS_HEADER = [
    "telegram_id", "mode", "started_at", "finished_at",
    "total_items", "correct_answers", "wrong_answers",
]


# ── gspread helpers (blocking) ────────────────────────────────────────────

def _open(cfg: Config):
    client = gspread.authorize(_creds(cfg))
    return client.open_by_key(cfg.spreadsheet_id)


def _ensure_sheet(ss, title: str, header: list[str]):
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=200, cols=max(len(header), 10))
        ws.update("A1", [header])
    return ws


# ── DUMP ──────────────────────────────────────────────────────────────────

def _dump_blocking(cfg: Config, users, vocab_p, verb_p, sessions) -> None:
    ss = _open(cfg)

    ws_u = _ensure_sheet(ss, USERS_SHEET, USERS_HEADER)
    ws_p = _ensure_sheet(ss, PROGRESS_SHEET, PROGRESS_HEADER)
    ws_s = _ensure_sheet(ss, SESSIONS_SHEET, SESSIONS_HEADER)

    # users
    users_rows = [USERS_HEADER] + [
        [u[0], u[1] or "", u[2] or "", u[3] or ""] for u in users
    ]
    ws_u.clear()
    ws_u.update("A1", users_rows, value_input_option="RAW")

    # progress (combined word + verb)
    prog_rows = [PROGRESS_HEADER]
    for r in vocab_p:
        # (telegram_id, word_id, status, correct, wrong, last_seen, next_review)
        prog_rows.append(["word", r[0], r[1], r[2], r[3], r[4],
                          "", "", r[5] or "", r[6] or ""])
    for r in verb_p:
        # (telegram_id, verb_id, status, correct, wrong, ps_err, pp_err,
        #  last_seen, next_review)
        prog_rows.append(["verb", r[0], r[1], r[2], r[3], r[4],
                          r[5], r[6], r[7] or "", r[8] or ""])
    ws_p.clear()
    ws_p.update("A1", prog_rows, value_input_option="RAW")

    # sessions
    sess_rows = [SESSIONS_HEADER] + [
        [s[0], s[1], s[2] or "", s[3] or "",
         s[4] or 0, s[5] or 0, s[6] or 0]
        for s in sessions
    ]
    ws_s.clear()
    ws_s.update("A1", sess_rows, value_input_option="RAW")


async def dump_to_sheets(cfg: Config) -> None:
    users    = await db.dump_users()
    vocab_p  = await db.dump_vocab_progress()
    verb_p   = await db.dump_verb_progress()
    sessions = await db.dump_sessions()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _dump_blocking, cfg, users, vocab_p, verb_p, sessions,
    )
    logger.info(
        "Snapshot pushed: %d users, %d word-progress, %d verb-progress, "
        "%d sessions", len(users), len(vocab_p), len(verb_p), len(sessions),
    )


# ── RESTORE ───────────────────────────────────────────────────────────────

def _restore_blocking(cfg: Config) -> tuple[list, list, list, list]:
    ss = _open(cfg)
    users: list[tuple] = []
    vocab_p: list[tuple] = []
    verb_p: list[tuple] = []
    sessions: list[tuple] = []

    try:
        rows = ss.worksheet(USERS_SHEET).get_all_records()
        for r in rows:
            try:
                users.append((
                    int(r["telegram_id"]),
                    r.get("username") or None,
                    r.get("first_name") or None,
                    r.get("created_at") or "",
                ))
            except (KeyError, ValueError, TypeError):
                continue
    except gspread.WorksheetNotFound:
        pass

    try:
        rows = ss.worksheet(PROGRESS_SHEET).get_all_records()
        for r in rows:
            try:
                tg = int(r["telegram_id"])
                item_id = str(r["item_id"])
                status = r.get("status") or "new"
                correct = int(r.get("correct_count") or 0)
                wrong   = int(r.get("wrong_count") or 0)
                last_seen   = r.get("last_seen_at") or None
                next_review = r.get("next_review_at") or None
                if r.get("type") == "word":
                    vocab_p.append((tg, item_id, status, correct, wrong,
                                    last_seen, next_review))
                elif r.get("type") == "verb":
                    ps = int(r.get("ps_errors") or 0)
                    pp = int(r.get("pp_errors") or 0)
                    verb_p.append((tg, item_id, status, correct, wrong,
                                   ps, pp, last_seen, next_review))
            except (KeyError, ValueError, TypeError):
                continue
    except gspread.WorksheetNotFound:
        pass

    try:
        rows = ss.worksheet(SESSIONS_SHEET).get_all_records()
        for r in rows:
            try:
                sessions.append((
                    int(r["telegram_id"]),
                    r.get("mode") or "vocab",
                    r.get("started_at") or "",
                    r.get("finished_at") or None,
                    int(r.get("total_items") or 0),
                    int(r.get("correct_answers") or 0),
                    int(r.get("wrong_answers") or 0),
                ))
            except (KeyError, ValueError, TypeError):
                continue
    except gspread.WorksheetNotFound:
        pass

    return users, vocab_p, verb_p, sessions


async def restore_from_sheets(cfg: Config) -> dict:
    loop = asyncio.get_running_loop()
    users, vocab_p, verb_p, sessions = await loop.run_in_executor(
        None, _restore_blocking, cfg,
    )
    await db.restore_users(users)
    await db.restore_vocab_progress(vocab_p)
    await db.restore_verb_progress(verb_p)
    await db.restore_sessions(sessions)
    logger.info(
        "Restored from sheets: %d users, %d word-progress, %d verb-progress, "
        "%d sessions",
        len(users), len(vocab_p), len(verb_p), len(sessions),
    )
    return {
        "users": len(users),
        "word_progress": len(vocab_p),
        "verb_progress": len(verb_p),
        "sessions": len(sessions),
    }
