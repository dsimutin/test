import json
import os
from typing import List, Tuple

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


def _col(header: List[str], *names) -> int | None:
    for name in names:
        for i, h in enumerate(header):
            if h == name:
                return i
    return None


def _val(row: List[str], idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    v = row[idx].strip()
    return v or None


def fetch_vocabulary() -> List[Tuple]:
    """
    Returns list of:
      (english, russian, transcription, example, card_type, past_simple, past_participle)

    Sheet 1 — words:
      columns: word/слово | translation/перевод | transcription/транскрипция | example/пример

    Sheet 2 — irregular verbs:
      columns: infinitive/инфинитив/v1 | translation/перевод |
               past simple/v2 | past participle/v3
    """
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    sheet_name = os.environ.get("SHEET_NAME", "vocabulary")

    client = gspread.authorize(_get_creds())
    spreadsheet = client.open_by_key(spreadsheet_id) if spreadsheet_id else client.open(sheet_name)

    result = []
    seen_words = set()
    seen_verbs = set()

    for idx, ws in enumerate(spreadsheet.worksheets()):
        rows = ws.get_all_values()
        if not rows:
            continue
        header = [h.strip().lower() for h in rows[0]]

        # Detect verb sheet by presence of past-simple column or by sheet index
        is_verb_sheet = (
            _col(header, "past simple", "past_simple", "прошедшее", "v2") is not None
            or idx == 1
        )

        if is_verb_sheet:
            inf_col = _col(header, "infinitive", "инфинитив", "word", "слово", "v1", "english")
            rus_col = _col(header, "translation", "перевод", "russian")
            ps_col  = _col(header, "past simple", "past_simple", "прошедшее", "v2")
            pp_col  = _col(header, "past participle", "past_participle", "причастие", "v3")

            if inf_col is None or rus_col is None:
                continue

            for row in rows[1:]:
                infinitive = _val(row, inf_col)
                russian    = _val(row, rus_col)
                if not infinitive or not russian or infinitive in seen_verbs:
                    continue
                seen_verbs.add(infinitive)
                result.append((
                    infinitive, russian, None, None, "verb",
                    _val(row, ps_col), _val(row, pp_col),
                ))
        else:
            eng_col = _col(header, "word", "слово", "english")
            rus_col = _col(header, "translation", "перевод", "russian")
            tr_col  = _col(header, "transcription", "транскрипция", "ipa")
            ex_col  = _col(header, "example", "пример")

            if eng_col is None or rus_col is None:
                continue

            for row in rows[1:]:
                english = _val(row, eng_col)
                russian = _val(row, rus_col)
                if not english or not russian or english in seen_words:
                    continue
                seen_words.add(english)
                result.append((
                    english, russian,
                    _val(row, tr_col),
                    _val(row, ex_col),
                    "word", None, None,
                ))

    return result
