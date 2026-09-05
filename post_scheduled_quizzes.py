"""
Telegram Quiz & Post Scheduler
--------------------------------
Reads a Google Spreadsheet with two tabs:

  Tab 1 "Quizzes" (the original sheet1 tab -- name doesn't matter, it's
  just whichever tab is first): quiz questions, posted as native
  Telegram quiz polls.

  Tab 2 "Posts" (must be named exactly "Posts"): plain text
  announcements or photo-with-caption posts -- for job notifications,
  exam updates, results, or anything that isn't a quiz.

Both tabs are optional in the sense that if "Posts" doesn't exist yet,
it's simply skipped with a note printed to the log -- this script
works fine with just the original Quizzes tab, same as before.

Designed to be run repeatedly (e.g. every 5 minutes via GitHub Actions
cron). Rows already marked "Yes" in their Posted column are always
skipped, so re-running never causes duplicate posts.

Required environment variables (set as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN        Bot token from @BotFather
  TELEGRAM_CHANNEL_ID       e.g. "@your_channel" or the numeric chat id
  GOOGLE_SERVICE_ACCOUNT_JSON   Full JSON key of the service account (as a string)
  GOOGLE_SHEET_ID           The long id in the sheet's URL between /d/ and /edit

Optional:
  TELEGRAM_ADMIN_CHAT_ID    Your personal Telegram chat id. If set, the bot
                             sends you a private message summarising any
                             errors from a run.

"Quizzes" tab columns (exact header names expected in row 1):
  Date | Time | Question | Option A | Option B | Option C | Option D |
  Correct Option | Explanation | Posted

"Posts" tab columns (exact header names expected in row 1):
  Date | Time | Type | Message | File URL | Posted

  (If you're upgrading from an earlier version of this sheet, just
  rename your existing "Image URL" column header to "File URL" --
  it's the same column, just also used for PDFs now, not only images.)

  Type must be exactly "Text", "Photo", or "Document".
    - Text posts: only "Message" is used (plain announcement).
    - Photo posts: "File URL" is required (a direct, publicly
      accessible link to the image -- see README for how to get one
      from Google Drive or a free image host), and "Message" becomes
      the photo's caption.
    - Document posts: "File URL" is required and MUST end in .pdf or
      .zip -- this is a hard limit from Telegram itself, not something
      this script can work around. Sending other file types (like a
      .docx) by URL isn't supported by Telegram's API; convert to PDF
      first. "Message" becomes the document's caption. Files over
      ~50MB may be rejected by Telegram -- compress large PDFs first.
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
TELEGRAM_TEXT_MESSAGE_MAX = 4096
TELEGRAM_PHOTO_CAPTION_MAX = 1024

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


def connect_spreadsheet():
    creds_json = get_env_or_die("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = get_env_or_die("GOOGLE_SHEET_ID")
    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"]
TIME_FORMATS = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"]


def parse_scheduled_datetime(date_str, time_str):
    """Parses the Date/Time columns. Tries DD/MM/YYYY first (our intended
    format) but falls back to a couple of other date layouts, and accepts
    both 24-hour and 12-hour (AM/PM, with or without seconds) time formats --
    Google Sheets silently rewrites what you typed into one of these even
    when you didn't ask it to, so the script tolerates all of them rather
    than requiring you to fight the sheet's auto-formatting."""
    date_str = date_str.strip()
    time_str = time_str.strip()

    parsed_date = None
    for fmt in DATE_FORMATS:
        try:
            parsed_date = datetime.strptime(date_str, fmt).date()
            break
        except ValueError:
            continue
    if parsed_date is None:
        raise ValueError(f"could not recognise date '{date_str}' in any supported format")

    parsed_time = None
    for fmt in TIME_FORMATS:
        try:
            parsed_time = datetime.strptime(time_str, fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError(f"could not recognise time '{time_str}' in any supported format")

    return datetime.combine(parsed_date, parsed_time, tzinfo=IST)


def send_admin_alert(message):
    """Sends a private text message to the admin chat, if TELEGRAM_ADMIN_CHAT_ID
    is configured. Silently does nothing if it isn't set."""
    admin_chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if not admin_chat_id:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url, data={"chat_id": admin_chat_id, "text": message}, timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"WARNING: could not send admin alert -- {data.get('description')}")
    except requests.RequestException as e:
        print(f"WARNING: could not send admin alert -- {e}")


# ---------------------------------------------------------------------------
# Quiz tab
# ---------------------------------------------------------------------------

def validate_quiz_row(row):
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
            f"Explanation is {len(row['Explanation'])} chars, exceeds Telegram's {TELEGRAM_EXPLANATION_MAX}-char limit"
        )
    correct = row.get("Correct Option", "").strip().upper()
    if correct not in ("A", "B", "C", "D"):
        problems.append(f"Correct Option must be A, B, C, or D (got: '{row.get('Correct Option')}')")
    return problems


def build_options(row):
    return [row[col].strip() for col in ["Option A", "Option B", "Option C", "Option D"] if row.get(col, "").strip()]


def send_quiz_poll(row):
    token = get_env_or_die("TELEGRAM_BOT_TOKEN")
    channel = get_env_or_die("TELEGRAM_CHANNEL_ID")

    options = build_options(row)
    correct_letter = row["Correct Option"].strip().upper()
    correct_index = ["A", "B", "C", "D"].index(correct_letter)
    if correct_index >= len(options):
        return False, f"Correct Option '{correct_letter}' has no matching option text"

    payload = {
        "chat_id": channel,
        "question": row["Question"].strip(),
        "options": json.dumps(options),
        "type": "quiz",
        "correct_option_id": correct_index,
        "is_anonymous": True,
    }
    explanation = row.get("Explanation", "").strip()
    if explanation:
        payload["explanation"] = explanation[:TELEGRAM_EXPLANATION_MAX]

    resp = requests.post(f"https://api.telegram.org/bot{token}/sendPoll", data=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        return False, data.get("description", "Unknown Telegram API error")
    return True, None


def process_quiz_tab(spreadsheet, now_ist):
    posted, skipped, errors, details = 0, 0, 0, []
    try:
        ws = spreadsheet.sheet1
    except gspread.exceptions.WorksheetNotFound:
        print("No quiz tab found -- skipping quiz processing.")
        return posted, skipped, errors, details

    rows = ws.get_all_records()
    for i, row in enumerate(rows):
        row_num = i + 2
        if str(row.get("Posted", "")).strip().lower() == "yes":
            continue
        if not row.get("Date") or not row.get("Time"):
            continue

        try:
            scheduled = parse_scheduled_datetime(str(row["Date"]), str(row["Time"]))
        except ValueError as e:
            print(f"[Quizzes] Row {row_num}: could not parse Date/Time ({e}) -- skipping")
            errors += 1
            details.append(f"[Quizzes] Row {row_num}: bad Date/Time format ({e})")
            continue

        if scheduled > now_ist:
            skipped += 1
            continue

        problems = validate_quiz_row(row)
        if problems:
            print(f"[Quizzes] Row {row_num}: NOT posted, validation failed: {'; '.join(problems)}")
            errors += 1
            details.append(f"[Quizzes] Row {row_num}: {'; '.join(problems)}")
            continue

        ok, error = send_quiz_poll(row)
        if ok:
            ws.update_cell(row_num, list(row.keys()).index("Posted") + 1, "Yes")
            print(f"[Quizzes] Row {row_num}: posted successfully.")
            posted += 1
        else:
            print(f"[Quizzes] Row {row_num}: FAILED to post -- {error}")
            errors += 1
            details.append(f"[Quizzes] Row {row_num}: Telegram rejected it -- {error}")

    return posted, skipped, errors, details


# ---------------------------------------------------------------------------
# Posts tab (text announcements / photo posts)
# ---------------------------------------------------------------------------

def get_file_url(row):
    """Reads the file URL from either 'File URL' (current column name) or
    'Image URL' (old column name), so upgrading doesn't break existing
    sheets that haven't renamed the header yet."""
    return (row.get("File URL") or row.get("Image URL") or "").strip()


def validate_post_row(row):
    problems = []
    post_type = row.get("Type", "").strip().lower()
    if post_type not in ("text", "photo", "document"):
        problems.append(f"Type must be 'Text', 'Photo', or 'Document' (got: '{row.get('Type')}')")
        return problems

    message = row.get("Message", "").strip()
    file_url = get_file_url(row)

    if post_type == "text":
        if not message:
            problems.append("Text posts need a Message")
        elif len(message) > TELEGRAM_TEXT_MESSAGE_MAX:
            problems.append(
                f"Message is {len(message)} chars, exceeds Telegram's {TELEGRAM_TEXT_MESSAGE_MAX}-char limit"
            )
    elif post_type == "photo":
        if not file_url:
            problems.append("Photo posts need a File URL")
        if len(message) > TELEGRAM_PHOTO_CAPTION_MAX:
            problems.append(
                f"Message (caption) is {len(message)} chars, exceeds Telegram's {TELEGRAM_PHOTO_CAPTION_MAX}-char "
                f"limit for photo captions"
            )
    else:  # document
        if not file_url:
            problems.append("Document posts need a File URL")
        elif not (file_url.lower().endswith(".pdf") or file_url.lower().endswith(".zip")):
            problems.append(
                "Document File URL must end in .pdf or .zip -- Telegram only supports sending "
                "documents by URL for these two file types (this is a Telegram limit, not a "
                "script limit). Other file types would need to be converted to PDF first."
            )
        if len(message) > TELEGRAM_PHOTO_CAPTION_MAX:
            problems.append(
                f"Message (caption) is {len(message)} chars, exceeds Telegram's {TELEGRAM_PHOTO_CAPTION_MAX}-char "
                f"limit for document captions"
            )
    return problems


def send_text_post(row):
    token = get_env_or_die("TELEGRAM_BOT_TOKEN")
    channel = get_env_or_die("TELEGRAM_CHANNEL_ID")
    payload = {
        "chat_id": channel,
        "text": row["Message"].strip(),
        "disable_web_page_preview": False,
    }
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        return False, data.get("description", "Unknown Telegram API error")
    return True, None


def send_photo_post(row):
    token = get_env_or_die("TELEGRAM_BOT_TOKEN")
    channel = get_env_or_die("TELEGRAM_CHANNEL_ID")
    payload = {
        "chat_id": channel,
        "photo": get_file_url(row),
    }
    caption = row.get("Message", "").strip()
    if caption:
        payload["caption"] = caption[:TELEGRAM_PHOTO_CAPTION_MAX]
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        return False, data.get("description", "Unknown Telegram API error")
    return True, None


def send_document_post(row):
    token = get_env_or_die("TELEGRAM_BOT_TOKEN")
    channel = get_env_or_die("TELEGRAM_CHANNEL_ID")
    payload = {
        "chat_id": channel,
        "document": get_file_url(row),
    }
    caption = row.get("Message", "").strip()
    if caption:
        payload["caption"] = caption[:TELEGRAM_PHOTO_CAPTION_MAX]
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendDocument", data=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        return False, data.get("description", "Unknown Telegram API error")
    return True, None


def process_posts_tab(spreadsheet, now_ist):
    posted, skipped, errors, details = 0, 0, 0, []
    try:
        ws = spreadsheet.worksheet("Posts")
    except gspread.exceptions.WorksheetNotFound:
        print("No 'Posts' tab found -- skipping (this is fine if you only use quizzes).")
        return posted, skipped, errors, details

    rows = ws.get_all_records()
    for i, row in enumerate(rows):
        row_num = i + 2
        if str(row.get("Posted", "")).strip().lower() == "yes":
            continue
        if not row.get("Date") or not row.get("Time"):
            continue

        try:
            scheduled = parse_scheduled_datetime(str(row["Date"]), str(row["Time"]))
        except ValueError as e:
            print(f"[Posts] Row {row_num}: could not parse Date/Time ({e}) -- skipping")
            errors += 1
            details.append(f"[Posts] Row {row_num}: bad Date/Time format ({e})")
            continue

        if scheduled > now_ist:
            skipped += 1
            continue

        problems = validate_post_row(row)
        if problems:
            print(f"[Posts] Row {row_num}: NOT posted, validation failed: {'; '.join(problems)}")
            errors += 1
            details.append(f"[Posts] Row {row_num}: {'; '.join(problems)}")
            continue

        post_type = row["Type"].strip().lower()
        if post_type == "text":
            ok, error = send_text_post(row)
        elif post_type == "photo":
            ok, error = send_photo_post(row)
        else:
            ok, error = send_document_post(row)
        if ok:
            ws.update_cell(row_num, list(row.keys()).index("Posted") + 1, "Yes")
            print(f"[Posts] Row {row_num}: posted successfully ({post_type}).")
            posted += 1
        else:
            print(f"[Posts] Row {row_num}: FAILED to post -- {error}")
            errors += 1
            details.append(f"[Posts] Row {row_num}: Telegram rejected it -- {error}")

    return posted, skipped, errors, details


# ---------------------------------------------------------------------------

def main():
    for name in REQUIRED_ENV:
        get_env_or_die(name)

    spreadsheet = connect_spreadsheet()
    now_ist = datetime.now(IST)

    q_posted, q_skipped, q_errors, q_details = process_quiz_tab(spreadsheet, now_ist)
    p_posted, p_skipped, p_errors, p_details = process_posts_tab(spreadsheet, now_ist)

    posted_count = q_posted + p_posted
    skipped_count = q_skipped + p_skipped
    error_count = q_errors + p_errors
    error_details = q_details + p_details

    print(
        f"\nSummary: {posted_count} posted, {skipped_count} not yet due, "
        f"{error_count} errors/skipped this run."
    )
    if error_count:
        alert_lines = [f"\u26a0\ufe0f Scheduler run had {error_count} problem(s):", ""]
        alert_lines.extend(error_details)
        alert_lines.append("")
        alert_lines.append(f"({posted_count} posted successfully this run, no action needed for those.)")
        send_admin_alert("\n".join(alert_lines))
        sys.exit(1)


if __name__ == "__main__":
    main()
