import os
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

DB_PATH = os.environ.get("DB_PATH", "flashcards.db")

LEARNED_THRESHOLD = 5  # correct answers to consider a card "learned"


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            english TEXT    NOT NULL,
            russian TEXT    NOT NULL,
            example TEXT,
            card_type TEXT  NOT NULL DEFAULT 'word'
        )
    """)
    # For irregular verbs: english=infinitive, russian=translation,
    # past_simple and past_participle stored in extra columns
    c.execute("PRAGMA table_info(words)")
    cols = {row[1] for row in c.fetchall()}
    if "past_simple" not in cols:
        c.execute("ALTER TABLE words ADD COLUMN past_simple TEXT")
    if "past_participle" not in cols:
        c.execute("ALTER TABLE words ADD COLUMN past_participle TEXT")

    # Unique index: (english, card_type)
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_words_unique
        ON words(english, card_type)
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            word_id         INTEGER NOT NULL REFERENCES words(id),
            ease_factor     REAL    DEFAULT 2.5,
            interval        INTEGER DEFAULT 0,
            repetitions     INTEGER DEFAULT 0,
            next_review     TEXT    DEFAULT (date('now')),
            last_review     TEXT,
            total_reviews   INTEGER DEFAULT 0,
            correct_reviews INTEGER DEFAULT 0,
            UNIQUE(user_id, word_id)
        )
    """)
    conn.commit()
    conn.close()


def sync_words(words: List[Tuple]) -> int:
    """
    Each item: (english, russian, example, card_type, past_simple, past_participle)
    card_type: 'word' | 'verb'
    """
    conn = _conn()
    c = conn.cursor()
    new_count = 0
    for row in words:
        english, russian, example, card_type, past_simple, past_participle = row
        c.execute(
            """
            INSERT OR IGNORE INTO words
                (english, russian, example, card_type, past_simple, past_participle)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                english.strip(),
                russian.strip(),
                example.strip() if example else None,
                card_type,
                past_simple.strip() if past_simple else None,
                past_participle.strip() if past_participle else None,
            ),
        )
        if c.rowcount:
            new_count += 1
    conn.commit()
    conn.close()
    return new_count


def get_due_cards(user_id: int, card_type: str, limit: int = 1) -> List[Tuple]:
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.id, w.english, w.russian, w.example, w.past_simple, w.past_participle
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        WHERE w.card_type = ?
          AND (
              up.id IS NULL
              OR (up.next_review <= ? AND COALESCE(up.correct_reviews, 0) < ?)
          )
        ORDER BY COALESCE(up.next_review, '0000-00-00'), RANDOM()
        LIMIT ?
        """,
        (user_id, card_type, today, LEARNED_THRESHOLD, limit),
    )
    result = c.fetchall()
    conn.close()
    return result


def get_or_create_progress(user_id: int, word_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO user_progress (user_id, word_id) VALUES (?, ?)",
        (user_id, word_id),
    )
    conn.commit()
    c.execute(
        "SELECT ease_factor, interval, repetitions FROM user_progress "
        "WHERE user_id = ? AND word_id = ?",
        (user_id, word_id),
    )
    row = c.fetchone()
    conn.close()
    return {"ease_factor": row[0], "interval": row[1], "repetitions": row[2]}


def update_progress(
    user_id: int,
    word_id: int,
    ease_factor: float,
    interval: int,
    repetitions: int,
    next_review: str,
    correct: int,
):
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        UPDATE user_progress SET
            ease_factor     = ?,
            interval        = ?,
            repetitions     = ?,
            next_review     = ?,
            last_review     = ?,
            total_reviews   = total_reviews + 1,
            correct_reviews = correct_reviews + ?
        WHERE user_id = ? AND word_id = ?
        """,
        (
            ease_factor,
            interval,
            repetitions,
            next_review,
            datetime.now().isoformat(),
            correct,
            user_id,
            word_id,
        ),
    )
    conn.commit()
    conn.close()


def get_user_stats(user_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT
            w.card_type,
            COUNT(DISTINCT w.id)                                        AS total,
            COUNT(CASE WHEN COALESCE(up.correct_reviews,0) >= ? THEN 1 END) AS learned,
            COALESCE(SUM(up.total_reviews), 0),
            COALESCE(SUM(up.correct_reviews), 0)
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        GROUP BY w.card_type
        """,
        (LEARNED_THRESHOLD, user_id),
    )
    rows = c.fetchall()
    conn.close()
    result = {}
    for card_type, total, learned, total_reviews, correct_reviews in rows:
        result[card_type] = {
            "total": total,
            "learned": learned,
            "total_reviews": total_reviews,
            "correct_reviews": correct_reviews,
        }
    return result


def get_card_counts(user_id: int) -> Dict:
    """Returns due count per card_type for today."""
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.card_type, COUNT(*)
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        WHERE up.id IS NULL
           OR (up.next_review <= ? AND COALESCE(up.correct_reviews, 0) < ?)
        GROUP BY w.card_type
        """,
        (user_id, today, LEARNED_THRESHOLD),
    )
    rows = c.fetchall()
    conn.close()
    return {card_type: count for card_type, count in rows}
