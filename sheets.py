import os
from typing import List, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def fetch_vocabulary() -> List[Tuple[str, str, Optional[str]]]:
    creds_file = os.environ["GOOGLE_CREDENTIALS_FILE"]
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    sheet_name = os.environ.get("SHEET_NAME", "vocabulary")

    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)

    if spreadsheet_id:
        spreadsheet = client.open_by_key(spreadsheet_id)
    else:
        spreadsheet = client.open(sheet_name)

    # Load from all sheets that contain vocabulary (skip utility sheets)
    SKIP_SHEETS = {"Progress Tracker"}
    words = []
    seen = set()

    for worksheet in spreadsheet.worksheets():
        if worksheet.title in SKIP_SHEETS:
            continue
        rows = worksheet.get_all_values()
        if not rows:
            continue

        # Detect column layout from header row
        header = [h.strip().lower() for h in rows[0]]
        try:
            eng_col = next(i for i, h in enumerate(header) if h in ("слово", "word", "english"))
            rus_col = next(i for i, h in enumerate(header) if h in ("перевод", "translation", "russian"))
        except StopIteration:
            continue
        ex_col = next(
            (i for i, h in enumerate(header) if h in ("пример", "example")), None
        )

        for row in rows[1:]:
            if len(row) <= max(eng_col, rus_col):
                continue
            english = row[eng_col].strip()
            russian = row[rus_col].strip()
            if not english or not russian or english in seen:
                continue
            example = row[ex_col].strip() if ex_col is not None and len(row) > ex_col else None
            seen.add(english)
            words.append((english, russian, example or None))

    return words
