"""CSV export of all student progress."""
import csv
import io
from datetime import date

from ..storage import db


async def build_progress_csv() -> tuple[bytes, str]:
    """Returns (csv_bytes, filename). UTF-8 with BOM so Excel opens it
    correctly on Windows / macOS."""
    vocab_rows, verb_rows = await db.export_all_progress()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "type", "telegram_id", "student", "username",
        "english", "russian", "extra1", "extra2",
        "lesson", "status",
        "correct", "wrong", "ps_errors", "pp_errors",
        "last_seen", "next_review",
    ])
    for r in vocab_rows:
        writer.writerow([
            "word", r["telegram_id"], r["student"], r["username"] or "",
            r["word"], r["translation"], "", "",
            r["lesson"], r["status"],
            r["correct"], r["wrong"], "", "",
            r["last_seen"] or "", r["next_review"] or "",
        ])
    for r in verb_rows:
        writer.writerow([
            "verb", r["telegram_id"], r["student"], r["username"] or "",
            r["infinitive"], r["translation"],
            r["past_simple"], r["past_participle"],
            r["lesson"], r["status"],
            r["correct"], r["wrong"], r["ps_errors"], r["pp_errors"],
            r["last_seen"] or "", r["next_review"] or "",
        ])

    data = ("﻿" + buf.getvalue()).encode("utf-8")
    filename = f"progress_{date.today().isoformat()}.csv"
    return data, filename
