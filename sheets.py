import json
import os
from datetime import date
from typing import List, Tuple

import gspread
from google.oauth2.service_account import Credentials

# Need write access to update progress sheet
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

PROGRESS_SHEET_NAME = "Прогресс учеников"
PROGRESS_HEADERS = ["Дата", "Ученик", "Telegram ID", "Слово / Глагол", "Тип", "Правильных ответов"]


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


def _open_spreadsheet():
    client = gspread.authorize(_get_creds())
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    sheet_name = os.environ.get("SHEET_NAME", "vocabulary")
    if spreadsheet_id:
        return client.open_by_key(spreadsheet_id)
    return client.open(sheet_name)


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


def _is_verb_sheet(ws, header: List[str]) -> bool:
    """Detect verb sheet by title or by presence of past-simple column."""
    title = ws.title.lower()
    if any(k in title for k in ("глагол", "verb", "irregular")):
        return True
    return _col(header, "past simple", "past_simple", "прошедшее", "v2") is not None


def fetch_vocabulary() -> List[Tuple]:
    """
    Returns list of:
      (english, russian, transcription, example, card_type, past_simple, past_participle)

    Sheet "Слова" (or first sheet):
      word/слово | translation/перевод | transcription/транскрипция | example/пример

    Sheet "Глаголы" (or second sheet):
      infinitive/глагол/v1 | translation/перевод | past simple/v2 | past participle/v3
    """
    spreadsheet = _open_spreadsheet()
    result = []
    seen_words = set()
    seen_verbs = set()

    for ws in spreadsheet.worksheets():
        # Skip the progress sheet
        if ws.title == PROGRESS_SHEET_NAME:
            continue

        rows = ws.get_all_values()
        if not rows:
            continue
        header = [h.strip().lower() for h in rows[0]]

        if _is_verb_sheet(ws, header):
            inf_col = _col(header,
                "infinitive", "инфинитив", "глагол", "word", "слово", "v1", "english")
            rus_col = _col(header, "translation", "перевод", "russian")
            ps_col  = _col(header, "past simple", "past_simple", "прошедшее", "v2")
            pp_col  = _col(header,
                "past participle", "past_participle", "причастие", "v3")

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


def write_learned_word(user_name: str, user_id: int, word: str, card_type: str, correct: int):
    """Append a row to the progress sheet when a word is learned."""
    try:
        spreadsheet = _open_spreadsheet()

        # Get or create progress sheet
        titles = [ws.title for ws in spreadsheet.worksheets()]
        if PROGRESS_SHEET_NAME not in titles:
            ws = spreadsheet.add_worksheet(
                title=PROGRESS_SHEET_NAME, rows=1000, cols=len(PROGRESS_HEADERS)
            )
            ws.append_row(PROGRESS_HEADERS)
        else:
            ws = spreadsheet.worksheet(PROGRESS_SHEET_NAME)

        type_label = "Слово" if card_type == "word" else "Глагол"
        ws.append_row([
            date.today().strftime("%d.%m.%Y"),
            user_name,
            str(user_id),
            word,
            type_label,
            str(correct),
        ])
    except Exception:
        logger.warning("Failed to write progress to sheets", exc_info=True)


# Logger for this module
import logging
logger = logging.getLogger(__name__)
