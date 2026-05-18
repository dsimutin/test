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

    rows = spreadsheet.sheet1.get_all_values()

    words = []
    for row in rows[1:]:  # skip header row
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            example = row[2].strip() if len(row) > 2 and row[2].strip() else None
            words.append((row[0], row[1], example))

    return words
