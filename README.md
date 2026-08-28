# Telegram Quiz Scheduler

Automatically posts quiz polls to your Telegram channel at scheduled
times, reading the queue from a shared Google Sheet. Runs for free on
GitHub Actions — nothing needs to stay running on your PC.

## How it works

1. You and your team add rows to a Google Sheet (one quiz per row, with a date/time).
2. Every 5 minutes, GitHub runs `post_scheduled_quizzes.py`.
3. The script checks the sheet for anything due, posts it to your Telegram channel as a native quiz poll, and marks the row "Posted".
4. Rows already marked "Posted" are never re-sent, even if the workflow runs again.

---

## Setup — do these once, in order

### Step 1 — Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `statsguru_quiz_bot`).
3. BotFather replies with a **token** — a long string like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Save it somewhere safe; you'll need it in Step 5.
4. Open your Telegram **channel** (create one first if you haven't), go to its settings → **Administrators** → **Add Admin**, and add your new bot. Make sure "Post Messages" permission is on.
5. Get your channel's ID:
   - If your channel is public, its ID is just `@yourchannelname`.
   - If it's private, forward any message from the channel to **@userinfobot** and it'll show you the numeric ID (looks like `-1001234567890`).

### Step 2 — Create the Google Sheet

1. Go to Google Sheets and create a new sheet.
2. In row 1, add these exact column headers (case-sensitive), in this order:
   ```
   Date | Time | Question | Option A | Option B | Option C | Option D | Correct Option | Explanation | Posted
   ```
3. Use the included `sheet_template.csv` as a reference for format — you can open it and copy the sample rows in, or just use it to see the expected format:
   - **Date**: `DD/MM/YYYY` (e.g. `28/08/2026`)
   - **Time**: 24-hour `HH:MM`, in IST (e.g. `09:00` or `18:30`)
   - **Correct Option**: just the letter `A`, `B`, `C`, or `D`
   - **Posted**: leave as `No` for new rows — the script fills in `Yes` automatically
4. Note the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/THIS_LONG_ID_HERE/edit` — save it for Step 5.

### Step 3 — Create a Google service account (so the script can read/write the sheet)

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create a new project (or use an existing one).
2. In the search bar, find **"Google Sheets API"** and click **Enable**.
3. Go to **IAM & Admin → Service Accounts → Create Service Account**. Give it any name (e.g. `quiz-scheduler`). Skip the optional permission/access steps.
4. Click into the service account you just made → **Keys** tab → **Add Key → Create new key → JSON**. This downloads a `.json` file — keep it safe, you'll paste its contents in Step 5.
5. Open that JSON file, find the `"client_email"` field (looks like `quiz-scheduler@your-project.iam.gserviceaccount.com`).
6. Go back to your Google Sheet → **Share** → paste that email address in → give it **Editor** access → Send. (This step is essential — without it, the script can't read or write your sheet.)

### Step 4 — Put the code on GitHub

1. Create a **private** GitHub repository (private, since your bot token and credentials will be referenced here — even though the actual secrets aren't stored in the code, keep the habit).
2. Upload these files, keeping the folder structure:
   ```
   post_scheduled_quizzes.py
   requirements.txt
   .github/workflows/quiz-scheduler.yml
   ```

### Step 5 — Add your secrets to GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add these four:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The token from Step 1.3 |
| `TELEGRAM_CHANNEL_ID` | The channel ID from Step 1.5 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Open the JSON file from Step 3.4, copy **the entire contents**, paste as the secret value |
| `GOOGLE_SHEET_ID` | The Sheet ID from Step 2.4 |

### Step 6 — Test it

1. Add one test row to your sheet with a **Date/Time a few minutes in the future**.
2. In your GitHub repo, go to the **Actions** tab → **Post Scheduled Telegram Quizzes** → **Run workflow** (this uses the manual trigger, so you don't have to wait for the real schedule).
3. Check your Telegram channel — the quiz poll should appear. Check the Actions log for any errors if it doesn't.
4. Once that works, just add rows to the sheet going forward — the automatic 5-minute schedule takes over from here, no more manual runs needed.

---

## Known limits (from Telegram itself, not this script)

- Question text: max **300 characters**
- Each option: max **100 characters**
- Explanation shown after answering: max **200 characters** (longer text gets silently cut off by Telegram, so the script also warns you if yours is too long)
- Only **one** correct answer per quiz (native Telegram quiz format doesn't support multiple correct options)
- Polls posted to a channel must be anonymous — Telegram doesn't allow non-anonymous polls in channels, so this is hardcoded in the script

If a row fails these checks, the script skips it, marks it as an error in the run log, and leaves "Posted" as "No" so you can fix the row and it'll go out next run — it never posts something broken or truncated silently.

## Adjusting the posting frequency

The `cron: "*/5 * * * *"` line in the workflow file controls how often GitHub checks for due quizzes. Every 5 minutes is a reasonable default. If you want tighter timing, you can go to `*/2 * * * *` (every 2 minutes), but GitHub's minimum supported interval is every 5 minutes for reliable execution — anything more frequent isn't guaranteed to actually run on time.
