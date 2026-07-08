import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_raw_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
try:
    TELEGRAM_CHAT_ID = int(_raw_chat_id)
except ValueError as e:
    raise RuntimeError(
        "Invalid TELEGRAM_CHAT_ID. Set TELEGRAM_CHAT_ID to your numeric Telegram chat id "
        "(e.g. 123456789) in your .env file, not the template placeholder."
    ) from e

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
COACH_NUTRITION_DOC_ID = os.environ["COACH_NUTRITION_DOC_ID"]
COACH_TRAINING_DOC_ID = os.environ["COACH_TRAINING_DOC_ID"]
WEEKLY_GOALS_DOC_ID = os.environ["WEEKLY_GOALS_DOC_ID"]

# Macro targets
DEFAULT_CALORIES = 1500
DEFAULT_PROTEIN = 130   # grams
DEFAULT_CARBS = 170     # grams
DEFAULT_FATS = 45       # grams
DEFAULT_SUGAR = 40      # grams

# Weekly gym target (Mon–Sun)
DEFAULT_GYM_SESSIONS_WEEK = 3
DEFAULT_CARDIO_SESSIONS_WEEK = 7   # 10k steps or cardio session every day
DEFAULT_CARDIO_MIN = 20            # minimum minutes to count as a cardio session

# Timezone
TIMEZONE = "Asia/Singapore"

# Sheet tab names
SHEET_FOOD = "Food Log"
SHEET_GYM = "Gym Log"
SHEET_SLEEP = "Sleep Log"
SHEET_SUMMARY = "Weekly Summary"
SHEET_EMOTIONS = "Emotions Log"
SHEET_ACTIVITY = "Activity Log"
SHEET_CYCLE = "Cycle Log"
SHEET_CATALOGUE = "Exercise Catalogue"
SHEET_BODY = "Body Log"
SHEET_CONTENT = "Content Log"
SHEET_REPORTS = "Reports Log"
SHEET_MICROS  = "Micronutrient Log"

# Female RDAs used for deficit reporting
MICRO_RDA = {
    "vitamin_a_ug":  700,
    "vitamin_c_mg":   75,
    "vitamin_d_ug":   15,
    "vitamin_e_mg":   15,
    "vitamin_b12_ug":  2.4,
    "folate_ug":     400,
    "calcium_mg":   1000,
    "iron_mg":        18,
    "magnesium_mg":  310,
    "zinc_mg":         8,
    "potassium_mg": 2600,
    "sodium_mg":    2300,
}

# Body metrics
HEIGHT_M = 1.64  # metres — used for BMI calculation

CLAUDE_MODEL = "claude-sonnet-4-6"
