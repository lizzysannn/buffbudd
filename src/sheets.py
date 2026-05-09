"""Google Sheets + Docs integration."""
import json
import os
from datetime import datetime, date
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from src.config import (
    GOOGLE_SERVICE_ACCOUNT_JSON, SPREADSHEET_ID,
    COACH_NUTRITION_DOC_ID, COACH_TRAINING_DOC_ID, WEEKLY_GOALS_DOC_ID,
    SHEET_FOOD, SHEET_GYM, SHEET_SLEEP, SHEET_SUMMARY,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _creds():
    # On Railway, credentials are stored as a JSON string in env var GOOGLE_CREDENTIALS_JSON
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    # Locally, use the file path
    return Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES)


def _sheet(tab: str):
    gc = gspread.authorize(_creds())
    return gc.open_by_key(SPREADSHEET_ID).worksheet(tab)


# ── Food Log ──────────────────────────────────────────────────────────────────

def log_food(meal_desc: str, calories: int, protein: float, carbs: float, fats: float):
    ws = _sheet(SHEET_FOOD)
    now = datetime.now()
    ws.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        meal_desc,
        calories,
        protein,
        carbs,
        fats,
    ])


def get_today_food() -> list[dict]:
    ws = _sheet(SHEET_FOOD)
    today = date.today().strftime("%Y-%m-%d")
    rows = ws.get_all_records()
    return [r for r in rows if str(r.get("Date", "")) == today]


def get_today_totals() -> dict:
    rows = get_today_food()
    return {
        "calories": sum(int(r.get("Calories", 0)) for r in rows),
        "protein": sum(float(r.get("Protein", 0)) for r in rows),
        "carbs": sum(float(r.get("Carbs", 0)) for r in rows),
        "fats": sum(float(r.get("Fats", 0)) for r in rows),
        "meals": len(rows),
    }


# ── Gym Log ───────────────────────────────────────────────────────────────────

def log_gym(exercise: str, sets: int, reps: int, weight: float, rpe: float | None, notes: str = ""):
    ws = _sheet(SHEET_GYM)
    now = datetime.now()
    ws.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        exercise,
        sets,
        reps,
        weight,
        rpe if rpe else "",
        notes,
    ])


def get_last_session(exercise: str) -> dict | None:
    ws = _sheet(SHEET_GYM)
    rows = ws.get_all_records()
    matches = [r for r in rows if str(r.get("Exercise", "")).lower() == exercise.lower()]
    if not matches:
        return None
    return matches[-1]


def get_pb(exercise: str) -> dict | None:
    ws = _sheet(SHEET_GYM)
    rows = ws.get_all_records()
    matches = [r for r in rows if str(r.get("Exercise", "")).lower() == exercise.lower()]
    if not matches:
        return None
    return max(matches, key=lambda r: float(r.get("Weight", 0)))


def get_today_gym() -> list[dict]:
    ws = _sheet(SHEET_GYM)
    today = date.today().strftime("%Y-%m-%d")
    rows = ws.get_all_records()
    return [r for r in rows if str(r.get("Date", "")) == today]


# ── Sleep Log ─────────────────────────────────────────────────────────────────

def log_sleep(hours: float, quality: int):
    ws = _sheet(SHEET_SLEEP)
    today = date.today().strftime("%Y-%m-%d")
    ws.append_row([today, hours, quality])


def get_sleep_streak() -> int:
    ws = _sheet(SHEET_SLEEP)
    rows = ws.get_all_records()
    if not rows:
        return 0
    streak = 0
    for row in reversed(rows):
        if float(row.get("Hours", 0)) >= 7:
            streak += 1
        else:
            break
    return streak


def get_today_sleep() -> dict | None:
    ws = _sheet(SHEET_SLEEP)
    today = date.today().strftime("%Y-%m-%d")
    rows = ws.get_all_records()
    matches = [r for r in rows if str(r.get("Date", "")) == today]
    return matches[-1] if matches else None


# ── Weekly Summary ────────────────────────────────────────────────────────────

def log_weekly_summary(data: dict):
    ws = _sheet(SHEET_SUMMARY)
    ws.append_row([
        data.get("week_start", ""),
        data.get("avg_calories", ""),
        data.get("avg_protein", ""),
        data.get("gym_sessions", ""),
        data.get("avg_sleep", ""),
        data.get("goal_score", ""),
        data.get("notes", ""),
    ])


def get_week_food() -> list[dict]:
    from datetime import timedelta
    ws = _sheet(SHEET_FOOD)
    rows = ws.get_all_records()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return [
        r for r in rows
        if str(r.get("Date", "")) >= week_start.strftime("%Y-%m-%d")
    ]


def get_week_gym() -> list[dict]:
    from datetime import timedelta
    ws = _sheet(SHEET_GYM)
    rows = ws.get_all_records()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return [
        r for r in rows
        if str(r.get("Date", "")) >= week_start.strftime("%Y-%m-%d")
    ]


# ── Google Docs ───────────────────────────────────────────────────────────────

def _read_doc(doc_id: str) -> str:
    service = build("docs", "v1", credentials=_creds())
    doc = service.documents().get(documentId=doc_id).execute()
    content = doc.get("body", {}).get("content", [])
    text_parts = []
    for block in content:
        paragraph = block.get("paragraph")
        if not paragraph:
            continue
        for elem in paragraph.get("elements", []):
            text_run = elem.get("textRun")
            if text_run:
                text_parts.append(text_run.get("content", ""))
    return "".join(text_parts).strip()


def get_coach_nutrition() -> str:
    return _read_doc(COACH_NUTRITION_DOC_ID)


def get_coach_training() -> str:
    return _read_doc(COACH_TRAINING_DOC_ID)


def get_weekly_goals() -> str:
    return _read_doc(WEEKLY_GOALS_DOC_ID)
