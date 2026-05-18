import json
import os
from typing import List, Tuple, Optional

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _get_creds() -> Credentials:
    raw = (
        os.environ.get("GOOGLE_CREDENTIALS_JSON")
        or os.environ.get("GOOGLE_CREDENTIALS")
        or os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    )
    if not raw:
        raise ValueError(
            "No Google credentials found. Set GOOGLE_CREDENTIALS env var "
            "to the service account JSON string."
        )
    if raw.strip().startswith("{"):
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return Credentials.from_service_account_file(raw, scopes=SCOPES)


def fetch_vocabulary() -> List[Tuple]:
    """
    Returns list of (english, russian, example, card_type, past_simple, past_participle).
    Sheet 1 — regular words:   columns: word | translation | example
    Sheet 2 — irregular verbs: columns: infinitive | translation | past_simple | past_participle
    """
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    sheet_name = os.environ.get("SHEET_NAME", "vocabulary")

    client = gspread.authorize(_get_creds())

    if spreadsheet_id:
        spreadsheet = client.open_by_key(spreadsheet_id)
    else:
        spreadsheet = client.open(sheet_name)

    worksheets = spreadsheet.worksheets()
    result = []
    seen_words = set()
    seen_verbs = set()

    for idx, ws in enumerate(worksheets):
        rows = ws.get_all_values()
        if not rows:
            continue

        header = [h.strip().lower() for h in rows[0]]

        # Detect sheet type: verbs sheet has past_simple / past_participle columns
        has_past = any(h in ("past simple", "past_simple", "прошедшее", "v2") for h in header)

        if has_past or idx == 1:
            # Irregular verbs sheet
            try:
                inf_col = next(
                    i for i, h in enumerate(header)
                    if h in ("infinitive", "инфинитив", "word", "слово", "v1")
                )
                rus_col = next(
                    i for i, h in enumerate(header)
                    if h in ("translation", "перевод", "russian")
                )
                ps_col = next(
                    i for i, h in enumerate(header)
                    if h in ("past simple", "past_simple", "прошедшее", "v2")
                )
                pp_col = next(
                    i for i, h in enumerate(header)
                    if h in ("past participle", "past_participle", "причастие", "v3")
                )
            except StopIteration:
                continue

            for row in rows[1:]:
                if len(row) <= max(inf_col, rus_col, ps_col, pp_col):
                    continue
                infinitive = row[inf_col].strip()
                russian = row[rus_col].strip()
                past_simple = row[ps_col].strip()
                past_participle = row[pp_col].strip()
                if not infinitive or not russian or infinitive in seen_verbs:
                    continue
                seen_verbs.add(infinitive)
                result.append((infinitive, russian, None, "verb", past_simple, past_participle))

        else:
            # Regular words sheet
            try:
                eng_col = next(
                    i for i, h in enumerate(header)
                    if h in ("word", "слово", "english")
                )
                rus_col = next(
                    i for i, h in enumerate(header)
                    if h in ("translation", "перевод", "russian")
                )
            except StopIteration:
                continue

            ex_col = next(
                (i for i, h in enumerate(header) if h in ("example", "пример")), None
            )

            for row in rows[1:]:
                if len(row) <= max(eng_col, rus_col):
                    continue
                english = row[eng_col].strip()
                russian = row[rus_col].strip()
                if not english or not russian or english in seen_words:
                    continue
                example = row[ex_col].strip() if ex_col is not None and len(row) > ex_col else None
                seen_words.add(english)
                result.append((english, russian, example or None, "word", None, None))

    return result
