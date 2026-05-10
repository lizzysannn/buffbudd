# Buff Buddy — Setup Guide

## 1. Telegram Bot Token

1. Open Telegram → search **@BotFather**
2. `/newbot` → follow prompts → copy the token
3. Get your chat ID: add **@userinfobot** to your group → it prints the chat ID (negative number for groups, positive for personal chat)

---

## 2. Anthropic API Key

1. Go to **console.anthropic.com** → sign up
2. API Keys → Create Key → copy it (`sk-ant-...`)
3. Add credits under Plans & Billing ($5 is plenty to start)

---

## 3. Google Cloud — Service Account

1. Go to **console.cloud.google.com** → create a new project
2. Enable these 3 APIs (APIs & Services → Enable APIs):
   - Google Sheets API
   - Google Drive API
   - Google Docs API
3. IAM & Admin → Service Accounts → Create
   - Name: `fitness-bot`, Role: Editor
4. Click the service account → Keys → Add Key → JSON → download
5. Rename the file to `credentials.json` and move it into the `fitness-bot/` folder
6. Copy the service account email (e.g. `fitness-bot@project-id.iam.gserviceaccount.com`)

---

## 4. Google Sheets — create & share

1. Create a new Google Sheet at **sheets.google.com**
2. Create these **7 tabs** (exact names, exact capitalisation):

| Tab name | Headers (one per cell in row 1) |
|---|---|
| `Food Log` | Date · Time · Meal Type · Meal · Calories · Protein · Carbs · Fats · Sugar (g) · Breakdown |
| `Gym Log` | Date · Time · Exercise · Sets · Reps · Weight · RPE · Notes · Type · Duration (min) |
| `Sleep Log` | Date · Hours · Quality |
| `Weekly Summary` | Week Start · Avg Calories · Avg Protein · Gym Sessions · Avg Sleep · Goal Score · Notes · Weight Start (kg) · Weight End (kg) · Weight Change (kg) · BF Start (%) · BF End (%) · Skeletal Muscle (kg) · Top Feel Tags |
| `Emotions Log` | Date · Time · Mood (1-10) · Energy (1-10) · Notes · Cycle Day · Phase |
| `Activity Log` | Date · Activity Type · Duration (mins) · Notes · Cycle Day · Phase |
| `Cycle Log` | Date · Cycle Day · Phase · Symptoms · Flow · Notes |
| `Body Log` | Date · Weight (kg) · Body Fat (%) · Body Feel · Notes |
| `Exercise Catalogue` | Exercise Name · Muscle Group · Set · Sets · Last Weight (kg) · Last Used · Notes |

> **Column notes:**
> - `Gym Log` col I = `Type` (`strength` or `cardio`) · col J = `Duration (min)` (cardio only)
> - `Food Log` col I = `Sugar (g)` · col J = `Breakdown` (auto-filled, stores per-item detail)
> - `Weekly Summary` cols H–N = body composition columns, auto-filled by Sunday weekly report
> - `Body Log` is logged when you send your morning weight/body feel check-in

3. Share the sheet with the service account email (Editor access)
4. Copy the Spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`

---

## 5. Google Docs — create & share

Create **3 Google Docs** at docs.google.com:

### CoachNutrition
Paste your coach's nutrition guidelines here. The bot reads this daily to personalise feedback.

### CoachTraining
Paste your training program here. Format must include a numbered exercise list:
```
SESSION: SELF TRAIN
7 exercises · 3 sets each

1. 75 Deg DB Shoulder Press — Upper
2. Barbell Deadlift — Pull / Hinge
3. Incline Leg Press — Legs
4. Mag Grip Pulldown — Pull
5. Cable Rope Tricep Extension — Arms
6. 75 Deg Seated DB Bicep Curl — Arms
7. Neutral Grip Cable Seated Row — Pull

BOT INSTRUCTIONS
Compare each logged exercise against previous session.
Flag PRs and regressions in the daily summary.
```

### WeeklyGoals
Write your goals for the week here. Example:
```
- Hit 115g protein 5 out of 7 days
- 3 gym sessions
- Sleep 7h+ each night
- Log all meals
```

For each doc:
1. Share with the service account email (Viewer access is fine)
2. Copy the Doc ID from the URL:
   `https://docs.google.com/document/d/THIS_IS_THE_DOC_ID/edit`

---

## 6. Configure .env

```bash
cp .env.template .env
```

Fill in all values:
```
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxx
TELEGRAM_CHAT_ID=-1001234567890
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SERVICE_ACCOUNT_JSON=credentials.json
SPREADSHEET_ID=1BxiMVs0XRA...
COACH_NUTRITION_DOC_ID=1abc...
COACH_TRAINING_DOC_ID=1def...
WEEKLY_GOALS_DOC_ID=1ghi...
```

---

## 7. Install & run locally

```bash
cd ~/Desktop/fitness-bot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

You should see `Buff Buddy starting...` — go message `/start` in your Telegram group.

---

## 8. Deploy to Railway (24/7 hosting)

1. Push code to GitHub (make sure `.env` and `credentials.json` are in `.gitignore`)
2. Go to **railway.app** → New Project → Deploy from GitHub repo
3. Select your repo → Railway auto-detects Python
4. Go to your service → **Variables tab** → add all `.env` values one by one
5. For `credentials.json` — run `cat credentials.json`, copy the entire JSON output, add as variable named `GOOGLE_CREDENTIALS_JSON`
6. Settings → Deploy → Start Command: `python main.py`
7. Deploy — Railway runs it 24/7

Railway auto-redeploys every time you `git push`.

---

## 9. How to use Buff Buddy

| What you want to log | What to send |
|---|---|
| Food (text) | Just describe it: `I had 100g chicken, rice and salad` |
| Food (photo) | Send the photo directly |
| Gym session | Send `GYM` → bot shows exercise list → log results |
| Sleep/Recovery | `7.5 4` (hours + quality 1-5) or just describe it naturally |
| Mood/Emotions | Tell it how you feel: `feeling tired and stressed today` |
| Period started | `period started` or any natural phrasing |
| Activity (walk, yoga etc.) | Describe it: `went for a 30 min walk` |
| Add exercise to catalogue | `add Romanian Deadlift` — bot looks up muscle group, asks to confirm |
| Create a new workout set | `create Push Day with Bench Press, OHP, Tricep Dips` |
| Target a muscle group | `I want to hit legs` — bot shows yours + suggests new ones |
| Move exercise to a set | `make Romanian Deadlift part of Self Train` |

The bot uses Claude to classify every message — no rigid commands needed. If it's unsure, it'll show buttons to pick the category.

After 8 seconds of silence it asks *"That everything for this one?"* — tap Yes to log or Add more to keep going.

---

## 10. Commands

| Command | What it does |
|---|---|
| `/summary` | Today's calories, protein, sleep, training, cycle day |
| `/week` | This week's averages |
| `/goals` | Your weekly goals from the doc |
| `/recovery` | Today's recovery status |
| `/streak` | Sleep streak (7h+ nights) |
| `/pb [exercise]` | Personal best e.g. `/pb Bench Press` |

---

## 11. Macro targets

Edit `src/config.py` to change your targets:
```python
DEFAULT_CALORIES = 1200
DEFAULT_PROTEIN  = 115
DEFAULT_CARBS    = 101
DEFAULT_FATS     = 32
```

---

## 12. Scheduled messages (SGT)

| Time | Message |
|---|---|
| 8:00 AM | Sleep check-in prompt |
| 9:00 PM | Daily summary push |
| Sunday 8:00 PM | Weekly report + goal scoring |
| On period start | Monthly cycle summary (previous cycle) |

---

## Troubleshooting

- **Bot not responding to messages** — check BotFather → `/mybots` → Bot Settings → Group Privacy → turn off
- **WorksheetNotFound error** — tab name doesn't match exactly (check capitalisation and spacing)
- **credentials.json not found** — make sure the file is in the `fitness-bot/` folder
- **Google API errors** — double-check the service account email is shared on the Sheet and all 3 Docs
- **Anthropic connection error** — transient network issue, try again; or check API key and credits
- **TELEGRAM_CHAT_ID wrong** — group chat IDs are negative numbers starting with `-100`
