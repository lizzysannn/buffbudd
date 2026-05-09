# Fitness Bot — Setup Guide

## 1. Revoke old token & create Telegram bot

1. Open Telegram → search **@BotFather**
2. If you have an old token: `/mybots` → select bot → *API Token* → *Revoke current token*
3. Create new: `/newbot` → follow prompts → copy the token
4. Get your chat ID:
  - Add **@userinfobot** to your group → it will print the chat ID (negative number for groups)
  - Or message your bot, then visit:
  `https://api.telegram.org/bot<TOKEN>/getUpdates`
  and look for `"chat":{"id":...}`

---

## 2. Google Cloud — Service Account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable these APIs (APIs & Services → Enable APIs):
  - **Google Sheets API**
  - **Google Drive API**
  - **Google Docs API**
4. Create a service account:
  - IAM & Admin → Service Accounts → Create
  - Name it e.g. `fitness-bot`
  - Role: **Editor** (or Basic → Editor)
5. Create a key:
  - Click the service account → Keys → Add Key → JSON
  - Download the file → rename to `credentials.json`
  - Move it into the `fitness-bot/` project directory
6. Copy the service account email (looks like `fitness-bot@project-id.iam.gserviceaccount.com`)

---

## 3. Google Sheets — create & share

1. Create a new Google Sheet
2. Create these 4 tabs (exact names):
  - `Food Log`
  - `Gym Log`
  - `Sleep Log`
  - `Weekly Summary`
3. Add headers to each tab:

**Food Log** (row 1):
`Date | Time | Meal | Calories | Protein | Carbs | Fats`

**Gym Log** (row 1):
`Date | Time | Exercise | Sets | Reps | Weight | RPE | Notes`

**Sleep Log** (row 1):
`Date | Hours | Quality`

**Weekly Summary** (row 1):
`Week Start | Avg Calories | Avg Protein | Gym Sessions | Avg Sleep | Goal Score | Notes`

1. Share the sheet with the service account email (Editor access)
2. Copy the Spreadsheet ID from the URL:
  `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`

---

## 4. Google Docs — create & share

Create 3 Google Docs:

- **CoachNutrition** — paste your coach's nutrition guidelines here
- **CoachTraining** — paste your coach's training guidelines here
- **WeeklyGoals** — write your goals for the week here (e.g. "Hit 180g protein 5 days, 3 gym sessions, sleep 7h+ each night")

For each doc:

1. Share with the service account email (Viewer access is fine)
2. Copy the Doc ID from the URL:
  `https://docs.google.com/document/d/THIS_IS_THE_DOC_ID/edit`

---

## 5. Configure .env

```bash
cp .env.template .env
```

Edit `.env` and fill in all values:

```
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxx
TELEGRAM_CHAT_ID=-100xxxxxxxxxx    # negative for group chats
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SERVICE_ACCOUNT_JSON=credentials.json
SPREADSHEET_ID=1BxiMVs0XRA...
COACH_NUTRITION_DOC_ID=1abc...
COACH_TRAINING_DOC_ID=1def...
WEEKLY_GOALS_DOC_ID=1ghi...
```

---

## 6. Install dependencies & run

```bash
cd fitness-bot
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

You should see `Bot starting...` — go message your bot `/start` to verify.

---

## 7. Customise targets

Edit `src/config.py` to change your macro targets:

```python
DEFAULT_CALORIES = 1200
DEFAULT_PROTEIN  = 115
DEFAULT_CARBS    = 101
DEFAULT_FATS     = 32
```

---

## 8. Scheduled messages (times in SGT)


| Time           | Message                      |
| -------------- | ---------------------------- |
| 8:00 AM        | Sleep check-in prompt        |
| 9:00 PM        | Daily summary push           |
| Sunday 8:00 PM | Weekly report + goal scoring |


---

## Troubleshooting

- `**TELEGRAM_CHAT_ID` not working** — make sure the bot is a member of the group, and the ID has the `-100` prefix for supergroups
- **Google API errors** — double-check the service account email is shared on the Sheet/Docs
- `**credentials.json` not found** — ensure it's in the same directory you run `python main.py` from
- **Photo analysis fails** — verify `ANTHROPIC_API_KEY` is valid and claude-sonnet-4-20250514 is accessible

