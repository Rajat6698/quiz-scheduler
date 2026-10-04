"""
Statsguru Site Watcher
----------------------
Checks job/exam websites for new notices, makes a branded Statsguru image
for each one, and sends it to you (the admin) on Telegram with buttons:

    ✅ Post to channel    ❌ Skip    🔗 Open

Nothing reaches the channel until you tap "Post to channel".
Before posting you can REPLY to the bot's message:
  - with details, one per line, to rebuild the poster, e.g.
        Vacancies: 149
        Last date: 20/10/2026
        Posts: Junior Engineer (Civil)
        Title: JE Civil 2026
    (other keys: Org, Type, Location, Background, Label, Tagline, Note;
     write "Key: -" to remove something)
  - with any other text, to replace the caption.

Runs on GitHub Actions (.github/workflows/site-watcher.yml). Each run:
  1. handles the buttons you tapped / replies you sent since the last run
  2. checks every active site listed in the "Watch Sites" tab of your sheet

Two tabs are added to your Google Sheet automatically on the first run:
  Watch Sites : the websites to check. Add a row to watch a new site,
                or set Active to "No" to pause one.
  Watch Log   : every notice found and what happened to it.

The very first check of a site only records what is already there, so you
don't get flooded with old notices. After that, only new ones are sent.

Uses the same GitHub secrets as the quiz scheduler:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_ADMIN_CHAT_ID,
  GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID
"""

import os
import re
import sys
import html
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urldefrag
from zoneinfo import ZoneInfo

import requests
import urllib3
from requests.utils import requote_uri
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

import poster

# ---------------------------------------------------------------- settings --

IST = ZoneInfo("Asia/Kolkata")
SIGNATURE = "— Admin: Stats Guru"

MAX_NEW_PER_SITE = 8        # more than this at once = probably a site redesign; sent as one list instead
FAIL_ALERT_AFTER = 3        # tell you when a site has failed this many checks in a row

DEFAULT_SITES = [
    # Name, Organisation, URL, Must contain, Share link, Active
    ["HPPSC Notices", "HP Public Service Commission",
     "https://www.hppsc.hp.gov.in/hppsc/Content/Index/?qlid=46&Ls_is=61&lngid=1", "", "Yes", "Yes"],
    ["HPPSC Advertisements", "HP Public Service Commission",
     "https://www.hppsc.hp.gov.in/hppsc/content/Index/?qlid=42&Ls_is=67&lngid=1", "", "Yes", "Yes"],
    ["HPRCA Notices", "HP Rajya Chayan Aayog, Hamirpur",
     "https://hprca.hp.gov.in/press-note-notice", "", "Yes", "Yes"],
    ["HP Police Notices", "HP Police",
     "https://citizenportal.hppolice.gov.in/citizen/viewnotbrfile.htm", "", "Yes", "Yes"],
    # Himexam is another coaching site: you get told about its new posts, but the
    # channel post never links to it. Find the official link before posting.
    ["Himexam", "", "https://himexam.com/", "himexam.com/", "No", "Yes"],
    # Public Telegram channels: use the https://t.me/s/<name> address. Only job/exam posts are sent.
    ["just5000 (Telegram)", "", "https://t.me/s/just5000", "", "No", "Yes"],
    ["allexam31 (Telegram)", "", "https://t.me/s/allexam31", "", "No", "Yes"],
]
SITE_HEADERS = ["Name", "Organisation", "URL", "Must contain", "Share link", "Active", "Last checked", "Result", "Failures"]
LOG_HEADERS = ["Found at", "Site", "Title", "Link", "Status", "Details"]

# (pattern, label, colour, emoji) — first match wins
CATEGORIES = [
    (r"answer\s*key|उत्तर\s*कुंजी", "ANSWER KEY", (111, 66, 193), "🔑"),
    (r"admit\s*card|e-?admit|call\s*letter|roll\s*no|प्रवेश\s*पत्र", "ADMIT CARD", (31, 111, 203), "🎫"),
    (r"result|merit\s*list|select(ed|ion)\s*list|final\s*list|qualified|shortlist|परिणाम|रिजल्ट", "RESULT", (43, 122, 75), "🏆"),
    (r"syllabus|scheme\s*of\s*exam", "SYLLABUS", (0, 121, 121), "📚"),
    (r"advertisement|recruitment|inviting|vacanc|bharti|भर्ती|\bora\b|apply\s*online", "NEW RECRUITMENT", (212, 42, 32), "📢"),
    (r"exam\s*date|schedule|date\s*sheet|personality\s*test|interview|postpone|re-?schedul|exam|परीक्षा", "EXAM UPDATE", (201, 121, 0), "🗓️"),
]
DEFAULT_CATEGORY = ("NOTIFICATION", (212, 42, 32), "📌")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}

# ------------------------------------------------------------------ basics --


def env(name):
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"ERROR: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return val


TOKEN = env("TELEGRAM_BOT_TOKEN")
CHANNEL = env("TELEGRAM_CHANNEL_ID")
ADMIN = env("TELEGRAM_ADMIN_CHAT_ID")
API = f"https://api.telegram.org/bot{TOKEN}"


class TelegramError(Exception):
    pass


def tg(method, files=None, **params):
    """Calls the Telegram Bot API and returns the result, or raises TelegramError."""
    if files:
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
        r = requests.post(f"{API}/{method}", data=data, files=files, timeout=60)
    else:
        r = requests.post(f"{API}/{method}", json=params, timeout=60)
    try:
        body = r.json()
    except ValueError:
        raise TelegramError(f"HTTP {r.status_code}")
    if not body.get("ok"):
        raise TelegramError(body.get("description", "unknown error"))
    return body["result"]


def say(text):
    """Sends you (the admin) a plain text message, split if too long."""
    while text:
        chunk, text = text[:4000], text[4000:]
        try:
            tg("sendMessage", chat_id=ADMIN, text=chunk, disable_web_page_preview=True)
        except TelegramError as e:
            print("Could not message admin:", e)
            return


def short(e, n=120):
    s = str(e).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def connect_sheet():
    info = json.loads(env("GOOGLE_SERVICE_ACCOUNT_JSON"))
    creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds).open_by_key(env("GOOGLE_SHEET_ID"))


def ensure_tab(book, title, headers, default_rows=None):
    try:
        ws = book.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.update([headers] + (default_rows or []), "A1", value_input_option="RAW")
        ws.freeze(rows=1)
        print(f"Created the '{title}' tab.")
        return ws
    first = ws.row_values(1)
    if not first:
        ws.update([headers] + (default_rows or []), "A1", value_input_option="RAW")
    elif len(first) < len(headers) and first == headers[:len(first)]:
        if ws.col_count < len(headers):
            ws.add_cols(len(headers) - ws.col_count)
        ws.update([headers], "A1", value_input_option="RAW")   # adds new columns such as "Details"
    return ws


# --------------------------------------------------------- reading sites --

SKIP_TEXT = re.compile(
    r"^(home|click here|read more|more|view|view all|view more|download|login|log in|register|sign up|"
    r"contact( us)?|about( us)?|sitemap|faq'?s?|privacy policy|terms.*|disclaimer|next|previous|prev|back|"
    r"english|hindi|हिंदी|skip to (main )?content|screen reader access|archive|go|search)$",
    re.I,
)


def clean_title(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"\s*\(?\b\d+(\.\d+)?\s*(KB|MB|kb|mb)\b\)?\s*$", "", t)   # "334KB" file-size suffix
    t = re.sub(r"\s*\bnew\b\s*$", "", t, flags=re.I)                      # "NEW" badge text
    return t.strip(" -–—:·|•")


def fetch(url):
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=45)
    except requests.exceptions.SSLError:
        # Several government sites have broken certificates; read them anyway.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=45, verify=False)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text, r.url


JOB_WORDS = re.compile(
    r"recruitment|vacanc|notification|bharti|भर्ती|result|परिणाम|admit\s*card|प्रवेश\s*पत्र|answer\s*key|"
    r"उत्तर\s*कुंजी|exam\s*date|syllabus|पाठ्यक्रम|apply\s*online|last\s*date|merit\s*list|"
    r"interview|cut\s*off|call\s*letter|advertisement|विज्ञापन",
    re.I,
)
EDGE_JUNK = re.compile(r"^[^\w\u0900-\u097F(]+|[^\w\u0900-\u097F).!?]+$")


def telegram_title(text):
    """Picks the most useful line of a Telegram post to use as its title."""
    lines = [EDGE_JUNK.sub("", l).strip() for l in text.splitlines()]
    lines = [re.sub(r"\s+", " ", l) for l in lines if len(l) >= 12]
    for line in lines:
        if JOB_WORDS.search(line):
            return line[:150]
    return lines[0][:150] if lines else ""


def extract_telegram_posts(html_text, must_contain=""):
    """Reads the public web view of a Telegram channel (https://t.me/s/<name>)."""
    soup = BeautifulSoup(html_text, "html.parser")
    out = []
    for msg in soup.select("div.tgme_widget_message[data-post]"):
        body = msg.select_one(".tgme_widget_message_text")
        if not body:
            continue
        for br in body.find_all("br"):
            br.replace_with("\n")
        text = body.get_text()
        if must_contain:
            if must_contain.lower() not in text.lower():
                continue
        elif not JOB_WORDS.search(text):
            continue            # skip quizzes, ads, motivation etc.
        title = telegram_title(text)
        if len(title) < 12:
            continue
        url = "https://t.me/" + msg["data-post"]
        out.append((url, title, url))
    return out


def extract_links(html, base_url, must_contain=""):
    """Returns [(key, title, url)] for every link that looks like a notice."""
    if re.match(r"https?://t\.me/", base_url):
        return extract_telegram_posts(html, must_contain)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = requote_uri(urldefrag(urljoin(base_url, href))[0])
        title = clean_title(a.get_text(" ") or a.get("title", ""))
        if len(title) < 15 or SKIP_TEXT.match(title):
            continue
        if must_contain and must_contain.lower() not in (url + " " + title).lower():
            continue
        found.append((title, url))

    # Usually a link's address identifies it. If one address is used for
    # several different titles on the page, identify by address + title.
    titles_per_url = {}
    for title, url in found:
        titles_per_url.setdefault(url, set()).add(title)
    out, seen = [], set()
    for title, url in found:
        key = url if len(titles_per_url[url]) == 1 else f"{url}|{title[:80]}"
        if key not in seen:
            seen.add(key)
            out.append((key, title, url))
    return out


def categorize(title):
    for pattern, label, colour, emoji in CATEGORIES:
        if re.search(pattern, title, re.I):
            return label, colour, emoji
    return DEFAULT_CATEGORY


# ------------------------------------------------------ poster + caption --

LABEL_TO_CATEGORY = {
    "NEW RECRUITMENT": "recruitment", "ADMIT CARD": "admit_card", "RESULT": "result",
    "ANSWER KEY": "answer_key", "EXAM UPDATE": "exam", "SYLLABUS": "syllabus", "NOTIFICATION": "general",
}
CATEGORY_TO_LABEL = {v: k for k, v in LABEL_TO_CATEGORY.items()}
CATEGORY_EMOJI = {"recruitment": "📢", "admit_card": "🎫", "result": "🏆", "answer_key": "🔑",
                  "exam": "🗓️", "syllabus": "📚", "general": "📌"}


def short_org(site_name):
    """'HPPSC Notices' -> 'HPPSC', 'HP Police Notices' -> 'HP Police'."""
    name = re.sub(r"\s+(notices?|advertisements?|results?|updates?|news)$", "", site_name.strip(), flags=re.I)
    return "" if name.lower() in ("himexam",) else name


def short_headline(title):
    """Turns a long official title into a short poster headline, usually the post or exam name."""
    t = re.sub(r"\s+", " ", title).strip()
    # HPPSC style: "Advertisement Number: 59/8-2026 (HP Administrative Service ... Examination-2026)"
    m = re.match(r"^advertisement\s*(?:number|no\.?)?\s*[:\-]?\s*[\d/\-]+\s*[:\-]?\s*\((.{6,})\)", t, re.I)
    if m:
        t = m.group(1)
    else:
        # "... to the Post(s) of Constable (Male & Female) for conduct of ..." -> "Constable (Male & Female)"
        m = re.search(r"\bposts?\s*(?:\(s\))?\s+(?:of\s+)?(.+)", t, re.I)
        if m and len(m.group(1)) > 3:
            t = m.group(1)
        else:
            prefixes = (r"advertisement(\s+notice)?\s*(number|no\.?)?\s*[:\-]?\s*[\d/\-]*", r"press\s*note",
                        r"notice", r"notification", r"corrigendum", r"regarding", r"reg\.", r"in respect of")
            changed = True
            while changed:
                before = t
                for pre in prefixes:
                    t = re.sub(r"^" + pre + r"\s*[:\-–]*\s*", "", t, flags=re.I).strip(" -–:.,")
                changed = t != before
    t = re.split(r"\s+in\s+the\s+", t, maxsplit=1, flags=re.I)[0]
    for long_form, short_form in ((r"combined\s+competitive\s+exam(ination)?", "CCE"), (r"examination", "Exam"),
                                  (r"department", "Dept."), (r"recruitment", "Recruitment")):
        t = re.sub(long_form, short_form, t, flags=re.I)
    t = re.split(r"\s+(?:for|in|under|vide|at|scheduled|to be|w\.e\.f\.?|with effect)\s+(?:the\s+)?"
                 r"(?:conduct|department|deptt|deptt\.|directorate|office|h\.p|hp|himachal|district|screening|"
                 r"personality|physical|recruitment|interview|exam)", t, maxsplit=1, flags=re.I)[0]
    t = t.strip(" -–:.,;")
    if len(t) > 46:
        out = ""
        for w in t.split():
            if len(out) + len(w) + 1 > 44:
                break
            out = (out + " " + w).strip()
        t = out.rstrip(",;:-–(&") or t[:44]
    if t.count("(") > t.count(")"):
        t += ")"
    return (t[:1].upper() + t[1:]) if t else title[:44]


def poster_fields(title, site, org_full, label, official=True):
    org, exam = (short_org(site) if official else ""), short_headline(title)
    if not org:
        # Titles like "HP High Court Recruitment 2026: Librarian" -> "HP High Court" + "Librarian Recruitment 2026"
        m = re.match(r"^(.{2,48}?)\s+((?:recruitment|bharti|result|admit\s*card|answer\s*key|exam\s*date|syllabus|"
                     r"notification|vacancy)\b.*)$", title.strip(), re.I)
        if m:
            org, rest = m.group(1).strip(" -–:"), m.group(2).strip()
            if ":" in rest:
                before, after = rest.split(":", 1)
                after = after.strip()
                useful = after and len(after) <= 28 and not re.match(
                    r"(check|download|apply|click|out|released|declared|here|link|notice|pdf|full)", after, re.I)
                rest = f"{after} {before.strip()}" if useful else before.strip()
            exam = short_headline(rest) if len(rest) > 44 else rest
    return {"category": LABEL_TO_CATEGORY.get(label, "general"), "org": org, "exam": exam, "org_full": org_full}


def fmt_date(v):
    dt = poster.parse_date(v)
    return dt.strftime("%d %b %Y") if dt else str(v)


# Order and labels of the job details shown in the caption.
CAPTION_DETAILS = [
    ("org_full", "🏛️ Organisation"), ("posts", "💼 Post"), ("vacancies", "👥 Vacancies"),
    ("qualification", "🎓 Qualification"), ("age", "🎂 Age limit"), ("salary", "💵 Salary"),
    ("fee", "💰 Application fee"), ("selection", "📝 Selection"), ("start_date", "🟢 Apply from"),
    ("last_date", "📅 Last date"), ("exam_date", "🗓️ Exam date"), ("apply_link", "🔗 Apply online"),
    ("website", "🌐 Official website"), ("extra", "ℹ️"),
]
DATE_FIELDS = ("start_date", "last_date", "exam_date")


def make_caption(info):
    """info = the Details saved for this notice (poster fields + title, url, share)."""
    f = info["fields"]
    cat = f.get("category", "general")
    head = f"{CATEGORY_EMOJI.get(cat, '📌')} {CATEGORY_TO_LABEL.get(cat, 'NOTIFICATION')}"
    facts = []
    for key, label in CAPTION_DETAILS:
        val = str(f.get(key, "")).strip()
        if not val:
            continue
        if key in DATE_FIELDS:
            val = fmt_date(val)
        facts.append(f"{label} {val}" if key == "extra" else f"{label}: {val}")
    if info.get("share") and info.get("url") and not f.get("apply_link"):
        facts.append(f"🔗 Notice: {info['url']}")
    tail = "\n".join(facts + ["", SIGNATURE]) if facts else SIGNATURE
    if len(tail) > 980:
        tail = tail[:970] + "…\n\n" + SIGNATURE
    room = 1024 - len(head) - len(tail) - 6
    title = info["title"]
    body = title if len(title) <= room else (title[: room - 1] + "…" if room > 20 else "")
    return "\n\n".join(p for p in (head, body, tail) if p)


def details_template(cat):
    """A fill-in list the bot sends under each poster, for copying into a reply."""
    if cat == "recruitment":
        keys = ["Vacancies", "Posts", "Qualification", "Age", "Salary", "Fee", "Apply from", "Last date", "Apply link"]
    elif cat in ("admit_card", "exam"):
        keys = ["Posts", "Exam date", "Link"]
    elif cat in ("result", "answer_key"):
        keys = ["Posts", "Last date", "Link"]
    else:
        keys = ["Posts", "Last date", "Link", "Info"]
    return "\n".join(k + ": " for k in keys)


# Words you can use when replying with details (left side of "Key: value").
DETAIL_KEYS = {
    "vacancies": "vacancies", "vacancy": "vacancies", "total posts": "vacancies", "पद": "vacancies",
    "posts": "posts", "post": "posts", "post name": "posts",
    "last date": "last_date", "lastdate": "last_date", "last": "last_date", "अंतिम तिथि": "last_date",
    "title": "exam", "headline": "exam", "exam": "exam",
    "org": "org", "organisation": "org", "organization": "org",
    "full name": "org_full", "department": "org_full",
    "type": "category", "category": "category",
    "location": "location", "state": "location",
    "background": "background", "bg": "background",
    "label": "vacancy_label", "vacancy label": "vacancy_label",
    "tagline": "tagline",
    "qualification": "qualification", "eligibility": "qualification", "education": "qualification", "योग्यता": "qualification",
    "age": "age", "age limit": "age", "आयु": "age",
    "salary": "salary", "pay": "salary", "pay scale": "salary", "वेतन": "salary",
    "fee": "fee", "fees": "fee", "application fee": "fee", "शुल्क": "fee",
    "selection": "selection", "selection process": "selection",
    "apply from": "start_date", "start date": "start_date", "starting date": "start_date",
    "exam date": "exam_date", "परीक्षा तिथि": "exam_date",
    "apply link": "apply_link", "link": "apply_link", "apply online": "apply_link",
    "website": "website", "official website": "website",
    "info": "extra", "extra": "extra", "details": "extra",
}
CATEGORY_WORDS = {
    "recruitment": "recruitment", "job": "recruitment", "vacancy": "recruitment", "bharti": "recruitment",
    "admit card": "admit_card", "admit": "admit_card", "result": "result", "answer key": "answer_key",
    "key": "answer_key", "exam": "exam", "exam update": "exam", "syllabus": "syllabus",
    "general": "general", "notice": "general", "update": "general",
}


def parse_details(text):
    """Returns {field: value} if the reply looks like 'Key: value' lines, else None."""
    found = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([^:=]{1,20}?)\s*[:=]\s*(.*)$", line)
        if not m:
            continue
        key = DETAIL_KEYS.get(m.group(1).strip().lower())
        if not key:
            continue
        val = m.group(2).strip()
        if key == "category":
            val = CATEGORY_WORDS.get(val.lower(), val.lower())
        if key in ("tagline", "note"):
            val = val.replace(" / ", "\n")
        found[key] = val
    return found or None


# ------------------------------------------------ your buttons & replies --

DETAILS_COL = 6


def keyboard(row_number, url, site):
    return {"inline_keyboard": [
        [{"text": "✅ Post to channel", "callback_data": f"post:{row_number}"},
         {"text": "❌ Skip", "callback_data": f"skip:{row_number}"}],
        [{"text": f"🔗 Open on {site}"[:60], "url": url}],
    ]}


def done_keyboard(old_markup, label):
    rows = [[{"text": label, "callback_data": "done"}]]
    for row in (old_markup or {}).get("inline_keyboard", []):
        if any("url" in b for b in row):
            rows.append(row)
    return {"inline_keyboard": rows}


def pending_row(markup):
    for row in (markup or {}).get("inline_keyboard", []):
        for b in row:
            if b.get("callback_data", "").startswith("post:"):
                return int(b["callback_data"].split(":")[1])
    return None


def handle_updates(log_ws):
    """Processes button taps and replies sent since the last run."""
    updates = tg("getUpdates", timeout=0, allowed_updates=["message", "callback_query"])
    print(f"Button taps / replies waiting: {len(updates)}")
    if not updates:
        return
    state = {"caption": {}, "photo": {}, "rows": set()}   # changes made during this run
    for u in updates:
        try:
            if "callback_query" in u:
                on_button(u["callback_query"], state, log_ws)
            elif "message" in u:
                on_reply(u["message"], state, log_ws)
        except Exception as e:
            print("Problem handling an update:", short(e, 300))
            say(f"⚠️ Something went wrong with your last action: {short(e)}")
    tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)   # mark them all as handled


def answer(q, text):
    try:
        tg("answerCallbackQuery", callback_query_id=q["id"], text=text)
    except TelegramError:
        pass   # taps older than a few minutes can't be answered; that's fine


def load_info(log_ws, row):
    raw = log_ws.cell(row, DETAILS_COL).value or ""
    try:
        return json.loads(raw)
    except ValueError:
        return None


def on_reply(m, state, log_ws):
    """A reply to a poster (or to the details list under it) changes that poster."""
    if str(m.get("chat", {}).get("id")) != ADMIN:
        return
    target = m.get("reply_to_message")
    text = (m.get("text") or "").strip()
    if not target or not text:
        return

    if "photo" in target:
        row = pending_row(target.get("reply_markup"))
        if not row:
            return
        info = load_info(log_ws, row)
        mid, markup = target["message_id"], target["reply_markup"]
        current_caption = state["caption"].get(mid, target.get("caption", ""))
    else:
        found = re.search(r"poster #(\d+)", target.get("text", ""))
        if not found:
            return
        row = int(found.group(1))
        info = load_info(log_ws, row)
        if not info or not info.get("msg_id"):
            return
        if (log_ws.cell(row, 5).value or "") in ("Posted", "Skipped"):
            say("That poster was already posted or skipped, so I didn't change it.")
            return
        mid = info["msg_id"]
        markup = keyboard(row, info["url"], log_ws.cell(row, 2).value or "site")
        current_caption = state["caption"].get(mid, info.get("caption") or make_caption(info))

    details = parse_details(text)
    if details and info:
        bad = [k for k in DATE_FIELDS if details.get(k) and not poster.parse_date(details[k])]
        if bad:
            say(f"I couldn't read the date \"{details[bad[0]]}\". Please write dates like 20/10/2026.")
            return
        for k, v in details.items():
            if v in ("", "-", "none", "remove"):
                info["fields"].pop(k, None)
            else:
                info["fields"][k] = v
        caption = info.get("caption") if info.get("custom_caption") else make_caption(info)
        caption = caption or current_caption
        image = poster.make_poster(info["fields"])
        media = {"type": "photo", "media": "attach://photo", "caption": caption}
        res = tg("editMessageMedia", files={"photo": ("poster.jpg", image, "image/jpeg")},
                 chat_id=ADMIN, message_id=mid, media=media, reply_markup=markup)
        state["photo"][mid] = res["photo"][-1]["file_id"]
        state["caption"][mid] = caption
        log_ws.update_cell(row, DETAILS_COL, json.dumps(info, ensure_ascii=False))
        filled = [k for k, v in details.items() if v not in ("", "-", "none", "remove")]
        changed = ", ".join(filled).replace("_", " ") or "details"
        tg("sendMessage", chat_id=ADMIN, reply_to_message_id=m["message_id"],
           text=f"🖼️ Poster and caption updated ({changed}). Tap ✅ Post to channel on the poster when you're ready.")
        return

    # anything else is treated as a new caption
    if len(text) > 1024:
        say(f"That caption is {len(text)} characters. Telegram allows 1024 under a photo. Please shorten it and reply again.")
        return
    tg("editMessageCaption", chat_id=ADMIN, message_id=mid, caption=text, reply_markup=markup)
    state["caption"][mid] = text
    if info:
        info["custom_caption"] = True
        info["caption"] = text
        log_ws.update_cell(row, DETAILS_COL, json.dumps(info, ensure_ascii=False))
    tg("sendMessage", chat_id=ADMIN, reply_to_message_id=m["message_id"],
       text="✏️ Caption updated. Tap ✅ Post to channel on the poster when you're ready.")


def find_row(log_ws, row, message_id):
    """The row a poster belongs to. Rows can move if someone deletes rows in the sheet,
    so check the saved message id and search for it if the row number is out of date."""
    def msg_id_at(values):
        try:
            return json.loads(values[DETAILS_COL - 1]).get("msg_id") if len(values) >= DETAILS_COL else None
        except (ValueError, AttributeError):
            return None
    values = log_ws.get_all_values()
    if 1 <= row <= len(values) and msg_id_at(values[row - 1]) in (message_id, None):
        if msg_id_at(values[row - 1]) == message_id or len(values[row - 1]) < DETAILS_COL:
            return row, values[row - 1]
    for i, v in enumerate(values[1:], start=2):
        if msg_id_at(v) == message_id:
            return i, v
    return (row, values[row - 1]) if 1 <= row <= len(values) else (None, None)


def on_button(q, state, log_ws):
    if str(q.get("from", {}).get("id")) != ADMIN:
        answer(q, "Only the admin can use these buttons.")
        return
    data, msg = q.get("data", ""), q.get("message", {})
    if not msg or ":" not in data:
        answer(q, "Done")
        return
    action, row = data.split(":", 1)
    mid = msg["message_id"]
    row, values = find_row(log_ws, int(row), mid)
    status = (values[4] if values and len(values) > 4 else "") or ""
    now = datetime.now(IST).strftime("%d %b, %H:%M")

    if row is None or row in state["rows"] or status in ("Posted", "Skipped"):
        label = f"✅ Already posted" if status == "Posted" else f"❌ Already skipped" if status == "Skipped" else "Already handled"
        answer(q, label)
        try:
            tg("editMessageReplyMarkup", chat_id=ADMIN, message_id=mid,
               reply_markup=done_keyboard(msg.get("reply_markup"), label))
        except TelegramError:
            pass
        print(f"Tap on message {mid} ignored: {label} (row {row}).")
        return
    state["rows"].add(row)

    if action == "post":
        caption = state["caption"].get(mid, msg.get("caption", ""))
        photo = state["photo"].get(mid, msg["photo"][-1]["file_id"])
        try:
            tg("sendPhoto", chat_id=CHANNEL, photo=photo, caption=caption)
        except TelegramError as e:
            say(f"⚠️ I couldn't post that to the channel. Telegram said: {e}\n"
                f"Check that the bot is still an admin of the channel with permission to post.")
            raise
        log_ws.update_cell(row, 5, "Posted")
        tg("editMessageReplyMarkup", chat_id=ADMIN, message_id=mid,
           reply_markup=done_keyboard(msg.get("reply_markup"), f"✅ Posted to channel · {now}"))
        answer(q, "Posted to the channel ✅")
        tg("sendMessage", chat_id=ADMIN, reply_to_message_id=mid, text="✅ Posted to your channel.")
        print(f"Posted row {row} to the channel.")
    elif action == "skip":
        log_ws.update_cell(row, 5, "Skipped")
        tg("editMessageReplyMarkup", chat_id=ADMIN, message_id=mid,
           reply_markup=done_keyboard(msg.get("reply_markup"), f"❌ Skipped · {now}"))
        answer(q, "Skipped")
        print(f"Skipped row {row}.")


# ---------------------------------------------------------- site checks --


def check_sites(sites_ws, log_ws, problems):
    sites = sites_ws.get_all_values()
    if len(sites) < 2:
        print("No sites listed in 'Watch Sites'.")
        return
    col = {h.strip(): i for i, h in enumerate(sites[0])}

    log = log_ws.get_all_values()
    known = {}
    for r in log[1:]:
        if len(r) >= 4:
            s = known.setdefault(r[1], set())
            s.add(r[3])
            s.add(f"{r[3]}|{r[2][:80]}")
    next_row = max(len(log), 1) + 1

    now = datetime.now(IST)
    stamp = now.strftime("%d/%m/%Y %H:%M")
    new_rows, to_send, site_cells, messages = [], [], [], []

    for sheet_row, r in enumerate(sites[1:], start=2):
        def get(h):
            i = col.get(h)
            return r[i].strip() if i is not None and i < len(r) else ""
        name, url = get("Name"), get("URL")
        if not name or not url:
            continue
        if get("Active").lower() in ("no", "n", "false", "0", "off"):
            continue
        prev_failures = int(get("Failures")) if get("Failures").isdigit() else 0
        print(f"Checking {name} ...")

        try:
            page, final_url = fetch(url)
            links = extract_links(page, final_url, get("Must contain"))
        except Exception as e:
            failures = prev_failures + 1
            result = f"Error: {short(e, 80)}"
            print(f"  {result}")
            if failures == FAIL_ALERT_AFTER:
                messages.append(f"⚠️ I couldn't open {name} for the last {failures} checks.\n"
                                f"Reason: {short(e)}\nThe site may be down. I'll keep trying and you'll hear from me when something new appears.")
            site_cells.append((sheet_row, stamp, result, failures))
            continue

        seen = known.get(name, set())
        fresh = [(k, t, u) for k, t, u in links if k not in seen]
        share = get("Share link").lower() not in ("no", "n", "false", "0")
        org = get("Organisation")

        is_tg_page = "tgme_widget_message" in page
        if not links and is_tg_page:
            result = "OK: no job posts in the latest messages"
            if not seen:
                new_rows.append([stamp, name, "(started watching)", url, "Marker"])
                messages.append(f"👀 Now watching {name}. There are no job posts in its latest messages, "
                                f"so you'll hear from me when the next one appears.")
        elif not links:
            result = "Opened, but found no notice links"
            if get("Result") != result:
                messages.append(f"🤔 I opened {name} but couldn't find any notice links on it. "
                                f"The page may load its list in a way I can't read. Send this to Claude to adjust.\n{url}")
        elif not seen:
            for k, t, u in links:
                new_rows.append([stamp, name, t, u, "Already there (first check)"])
            result = f"First check: recorded {len(links)} existing notices"
            messages.append(f"👀 Now watching {name}. I recorded the {len(links)} notices already on it, "
                            f"so you'll only hear about new ones from now on.")
        elif len(fresh) > MAX_NEW_PER_SITE:
            lines = [f"🗂️ {name} shows {len(fresh)} notices I haven't seen before. That usually means the site was "
                     f"redesigned, so I'm listing them instead of making images:\n"]
            for k, t, u in fresh:
                new_rows.append([stamp, name, t, u, "Listed (too many at once)"])
                lines.append(f"• {t}\n  {u}")
            messages.append("\n".join(lines))
            result = f"OK: {len(fresh)} new (listed)"
        else:
            for k, t, u in fresh:
                label = categorize(t)[0]
                info = {"title": t, "url": u, "share": share, "fields": poster_fields(t, name, org, label, share)}
                new_rows.append([stamp, name, t, u, "Waiting for you", json.dumps(info, ensure_ascii=False)])
                to_send.append({"row": next_row + len(new_rows) - 1, "site": name, "info": info})
            result = f"OK: {len(links)} notices, {len(fresh)} new"
        print(f"  {result}")
        site_cells.append((sheet_row, stamp, result, 0))

    if new_rows:
        log_ws.append_rows(new_rows, value_input_option="RAW")

    for item in to_send:
        info = item["info"]
        try:
            image = poster.make_poster(info["fields"])
            sent = tg("sendPhoto", files={"photo": ("poster.jpg", image, "image/jpeg")}, chat_id=ADMIN,
                      caption=make_caption(info), reply_markup=keyboard(item["row"], info["url"], item["site"]))
            print(f"  Sent for approval: {info['title'][:70]}")
            info["msg_id"] = sent["message_id"]
            log_ws.update_cell(item["row"], 6, json.dumps(info, ensure_ascii=False))
            template = details_template(info["fields"].get("category"))
            tg("sendMessage", chat_id=ADMIN, reply_to_message_id=sent["message_id"], parse_mode="HTML",
               text="✍️ <b>Add job details:</b> tap the list below to copy it, then <b>reply to this message</b> "
                    "with it filled in. Leave out anything you don't know.\n\n<pre>" + html.escape(template) + "</pre>"
                    f"\n<i>poster #{item['row']}</i>")
        except Exception as e:
            problems.append(f"Could not send '{info['title'][:60]}': {short(e)}")
            log_ws.update_cell(item["row"], 5, f"Could not send: {short(e, 60)}")

    for text in messages:
        say(text)

    if site_cells:
        c = {h: col[h] + 1 for h in ("Last checked", "Result", "Failures") if h in col}
        updates = []
        for sheet_row, checked, result, failures in site_cells:
            for h, v in (("Last checked", checked), ("Result", result), ("Failures", failures)):
                if h in c:
                    updates.append({"range": gspread.utils.rowcol_to_a1(sheet_row, c[h]), "values": [[v]]})
        sites_ws.batch_update(updates, value_input_option="RAW")


# ------------------------------------------------------------------ main --


def main():
    book = connect_sheet()
    sites_ws = ensure_tab(book, "Watch Sites", SITE_HEADERS, DEFAULT_SITES)
    log_ws = ensure_tab(book, "Watch Log", LOG_HEADERS)
    poster.ensure_fonts()

    failed = False
    try:
        handle_updates(log_ws)
    except TelegramError as e:
        failed = True
        print(f"ERROR reading your button taps: {e}", file=sys.stderr)
        if "webhook" in str(e).lower() or "conflict" in str(e).lower():
            print("Another program is receiving this bot's updates (a webhook or another getUpdates). "
                  "Buttons can't work until that stops.", file=sys.stderr)
            if datetime.now(IST).minute < 10:      # remind at most about once an hour
                say("⚠️ I can't see your button taps: another service is connected to this bot "
                    f"(Telegram says: {e}). Send this message to Claude to fix it.")

    problems = []
    check_sites(sites_ws, log_ws, problems)
    if problems:
        say("⚠️ Site watcher problems:\n\n" + "\n".join("• " + p for p in problems))
        failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
