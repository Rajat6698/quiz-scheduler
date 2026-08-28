"""
Telegram Quiz Scheduler
------------------------
Reads a Google Sheet of quiz questions, finds any whose scheduled
date/time has arrived and hasn't been posted yet, sends them to a
Telegram channel as native quiz polls, and marks them posted.

Designed to be run repeatedly (e.g. every 5 minutes via GitHub Actions
cron). It is safe to run more often than needed -- rows already marked
"Yes" in the Posted column are skipped, so there is no duplicate-post
risk even if two runs overlap slightly.

Required environment variables (set as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN        Bot token from @BotFather
  TELEGRAM_CHANNEL_ID       e.g. "@your_channel" or the numeric chat id
  GOOGLE_SERVICE_ACCOUNT_JSON   Full JSON key of the service account (as a string)
  GOOGLE_SHEET_ID           The long id in the sheet's URL between /d/ and /edit

Sheet columns (exact header names expected in row 1):
  Date | Time | Question | Option A | Option B | Option C | Option D |
  Correct Option | Explanation | Posted
"""

import os
import sys
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import gspread
from google.oauth2.service_account import Credentials

IST = ZoneInfo("Asia/Kolkata")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

TELEGRAM_QUESTION_MAX = 300
TELEGRAM_OPTION_MAX = 100
TELEGRAM_EXPLANATION_MAX = 200

REQUIRED_ENV = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_SHEET_ID",
]


def get_env_or_die(name):
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return val


def connect_sheet():
    creds_json = get_env_or_die("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = get_env_or_die("GOOGLE_SHEET_ID")
    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id).sheet1
    return sheet


def parse_scheduled_datetime(date_str, time_str):
    """Sheet stores Date as DD/MM/YYYY and Time as HH:MM (24-hour), both in IST."""
    combined = f"{date_str.strip()} {time_str.strip()}"
    dt = datetime.strptime(combined, "%d/%m/%Y %H:%M")
    return dt.replace(tzinfo=IST)


def validate_row(row, row_num):
    """Returns a list of problems with this row (empty list = valid)."""
    problems = []
    if len(row["Question"]) > TELEGRAM_QUESTION_MAX:
        problems.append(
            f"Question is {len(row['Question'])} chars, exceeds Telegram's {TELEGRAM_QUESTION_MAX}-char limit"
        )
    for col in ["Option A", "Option B", "Option C", "Option D"]:
        if row[col] and len(row[col]) > TELEGRAM_OPTION_MAX:
            problems.append(
                f"{col} is {len(row[col])} chars, exceeds Telegram's {TELEGRAM_OPTION_MAX}-char limit"
            )
    if row.get("Explanation") and len(row["Explanation"]) > TELEGRAM_EXPLANATION_MAX:
        problems.append(
            f"Explanation is {len(row['Explanation'])} chars, exceeds Telegram's {TELEGRAM_EXPLANATION_MAX}-char limit "
            f"(it will be sent truncated by Telegram itself)"
        )
    correct = row.get("Correct Option", "").strip().upper()
    if correct not in ("A", "B", "C", "D"):
        problems.append(f"Correct Option must be A, B, C, or D (got: '{row.get('Correct Option')}')")
    return problems


def build_options(row):
    options = []
    for col in ["Option A", "Option B", "Option C", "Option D"]:
        val = row.get(col, "").strip()
        if val:
            options.append(val)
    return options


def send_quiz_poll(row):
    token = get_env_or_die("TELEGRAM_BOT_TOKEN")
    channel = get_env_or_die("TELEGRAM_CHANNEL_ID")

    options = build_options(row)
    correct_letter = row["Correct Option"].strip().upper()
    correct_index = ["A", "B", "C", "D"].index(correct_letter)
    # correct_index must be valid for however many options actually exist
    if correct_index >= len(options):
        return False, f"Correct Option '{correct_letter}' has no matching option text"

    payload = {
        "chat_id": channel,
        "question": row["Question"].strip(),
        "options": json.dumps(options),
        "type": "quiz",
        "correct_option_id": correct_index,
        "is_anonymous": True,  # Telegram requires anonymous polls in channels
    }
    explanation = row.get("Explanation", "").strip()
    if explanation:
        payload["explanation"] = explanation[:TELEGRAM_EXPLANATION_MAX]

    url = f"https://api.telegram.org/bot{token}/sendPoll"
    resp = requests.post(url, data=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        return False, data.get("description", "Unknown Telegram API error")
    return True, None


def main():
    for name in REQUIRED_ENV:
        get_env_or_die(name)

    sheet = connect_sheet()
    rows = sheet.get_all_records()  # list of dicts keyed by header row
    now_ist = datetime.now(IST)

    posted_count = 0
    skipped_count = 0
    error_count = 0

    for i, row in enumerate(rows):
        row_num = i + 2  # +2 because row 1 is the header and gspread rows are 1-indexed
        posted_flag = str(row.get("Posted", "")).strip().lower()

        if posted_flag == "yes":
            continue  # already posted, nothing to do

        if not row.get("Date") or not row.get("Time"):
            continue  # blank row, ignore silently

        try:
            scheduled = parse_scheduled_datetime(str(row["Date"]), str(row["Time"]))
        except ValueError as e:
            print(f"Row {row_num}: could not parse Date/Time ({e}) -- skipping")
            error_count += 1
            continue

        if scheduled > now_ist:
            skipped_count += 1
            continue  # not due yet

        problems = validate_row(row, row_num)
        if problems:
            print(f"Row {row_num}: NOT posted, validation failed:")
            for p in problems:
                print(f"    - {p}")
            error_count += 1
            continue

        ok, error = send_quiz_poll(row)
        if ok:
            sheet.update_cell(row_num, list(row.keys()).index("Posted") + 1, "Yes")
            print(f"Row {row_num}: posted successfully.")
            posted_count += 1
        else:
            print(f"Row {row_num}: FAILED to post -- {error}")
            error_count += 1

    print(
        f"\nSummary: {posted_count} posted, {skipped_count} not yet due, "
        f"{error_count} errors/skipped this run."
    )
    if error_count:
        sys.exit(1)  # makes the GitHub Actions run show as failed, so you notice


if __name__ == "__main__":
    main()
