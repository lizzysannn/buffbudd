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
    SHEET_EMOTIONS, SHEET_ACTIVITY, SHEET_CYCLE, SHEET_CATALOGUE,
    SHEET_BODY, SHEET_CONTENT, SHEET_REPORTS, SHEET_MICROS, HEIGHT_M,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _norm_date(d) -> str:
    """Normalize any date string to YYYY-MM-DD with zero-padded month/day."""
    try:
        parts = str(d).strip().split("-")
        if len(parts) == 3:
            return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        pass
    return str(d).strip()


def _norm_row(row: dict) -> dict:
    """Strip leading/trailing whitespace from all keys in a sheet row."""
    return {k.strip(): v for k, v in row.items()}


def _get_sugar(row: dict) -> float:
    """Read sugar value robustly — handles 'Sugar (g)' and 'Sugar (g) ' (trailing space)."""
    r = _norm_row(row)
    return float(r.get("Sugar (g)", 0) or 0)


def _creds():
    # Check both possible env var names for the JSON string
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if raw and raw.strip().startswith("{"):
        # It's a JSON string — use it directly (Railway deployment)
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    # It's a file path — use it as-is (local development)
    path = raw or GOOGLE_SERVICE_ACCOUNT_JSON
    return Credentials.from_service_account_file(path, scopes=SCOPES)


def _sheet(tab: str):
    gc = gspread.authorize(_creds())
    return gc.open_by_key(SPREADSHEET_ID).worksheet(tab)


def _get_records(ws) -> list[dict]:
    """Safe get_all_records that handles duplicate or empty header columns."""
    all_values = ws.get_all_values()
    if not all_values:
        return []
    raw_headers = all_values[0]
    # Deduplicate headers by appending _2, _3 etc. so gspread dict-building works
    seen: dict[str, int] = {}
    headers = []
    for h in raw_headers:
        h = h.strip()
        if h in seen:
            seen[h] += 1
            headers.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 1
            headers.append(h)
    return [dict(zip(headers, row)) for row in all_values[1:]]


# ── Food Log ──────────────────────────────────────────────────────────────────

def infer_meal_type_from_time() -> str:
    """Infer meal type from current hour."""
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "breakfast"
    elif 11 <= hour < 15:
        return "lunch"
    elif 15 <= hour < 18:
        return "snack"
    elif 18 <= hour < 22:
        return "dinner"
    else:
        return "supper"


def log_food(meal_desc: str, calories: int, protein: float, carbs: float, fats: float,
             meal_type: str = "", log_date: str = "", sugar: float = 0.0, breakdown: str = "",
             micros: dict | None = None):
    ws = _sheet(SHEET_FOOD)
    now = datetime.now()
    row_date = log_date or now.strftime("%Y-%m-%d")
    row_time = now.strftime("%H:%M") if not log_date else ""
    m = micros or {}
    ws.append_row([
        row_date, row_time,
        meal_type or infer_meal_type_from_time(),
        meal_desc,
        calories, protein, carbs, fats, sugar,
        breakdown,
        # Micronutrient columns K–V
        m.get("vitamin_a_ug", ""),
        m.get("vitamin_c_mg", ""),
        m.get("vitamin_d_ug", ""),
        m.get("vitamin_e_mg", ""),
        m.get("vitamin_b12_ug", ""),
        m.get("folate_ug", ""),
        m.get("calcium_mg", ""),
        m.get("iron_mg", ""),
        m.get("magnesium_mg", ""),
        m.get("zinc_mg", ""),
        m.get("potassium_mg", ""),
        m.get("sodium_mg", ""),
    ])


def delete_last_food_row():
    """Remove the most recently logged food entry (for corrections)."""
    ws = _sheet(SHEET_FOOD)
    all_rows = ws.get_all_values()
    if len(all_rows) > 1:  # keep header
        ws.delete_rows(len(all_rows))


def get_recent_meal_descriptions(meal_type: str = "", limit: int = 8) -> list[str]:
    """Return recent unique meal descriptions for context, optionally filtered by meal type."""
    ws = _sheet(SHEET_FOOD)
    rows = _get_records(ws)
    if meal_type:
        rows = [r for r in rows if str(r.get("Meal Type", "")).lower() == meal_type.lower()]
    # Most recent first, deduplicated
    seen, result = set(), []
    for r in reversed(rows):
        desc = str(r.get("Meal", "")).strip()
        if desc and desc not in seen:
            seen.add(desc)
            result.append(desc)
        if len(result) >= limit:
            break
    return result


def get_last_meal_entry(meal_type: str = "") -> dict | None:
    """Return the most recent food log row (with macros) for the given meal type."""
    ws = _sheet(SHEET_FOOD)
    rows = _get_records(ws)
    if meal_type:
        rows = [r for r in rows if str(r.get("Meal Type", "")).lower() == meal_type.lower()]
    if not rows:
        return None
    return rows[-1]  # Most recent


def get_food_by_date(date_str: str) -> list[dict]:
    ws = _sheet(SHEET_FOOD)
    rows = _get_records(ws)
    target = _norm_date(date_str)
    return [r for r in rows if _norm_date(r.get("Date", "")) == target]


def get_today_food() -> list[dict]:
    ws = _sheet(SHEET_FOOD)
    today = _norm_date(date.today().isoformat())
    rows = _get_records(ws)
    return [r for r in rows if _norm_date(r.get("Date", "")) == today]


def get_today_totals() -> dict:
    rows = get_today_food()
    return {
        "calories": sum(int(r.get("Calories", 0)) for r in rows),
        "protein": sum(float(r.get("Protein", 0)) for r in rows),
        "carbs": sum(float(r.get("Carbs", 0)) for r in rows),
        "fats": sum(float(r.get("Fats", 0)) for r in rows),
        "sugar": sum(_get_sugar(r) for r in rows),
        "meals": len(rows),
    }


_MICRO_SHEET_COLS = {
    "vitamin_a_ug":  "Vitamin A (µg)",
    "vitamin_c_mg":  "Vitamin C (mg)",
    "vitamin_d_ug":  "Vitamin D (µg)",
    "vitamin_e_mg":  "Vitamin E (mg)",
    "vitamin_b12_ug":"B12 (µg)",
    "folate_ug":     "Folate (µg)",
    "calcium_mg":    "Calcium (mg)",
    "iron_mg":       "Iron (mg)",
    "magnesium_mg":  "Magnesium (mg)",
    "zinc_mg":       "Zinc (mg)",
    "potassium_mg":  "Potassium (mg)",
    "sodium_mg":     "Sodium (mg)",
}

def get_today_micro_totals() -> dict:
    """Sum all micro columns across today's Food Log rows."""
    rows = get_today_food()
    totals = {k: 0.0 for k in _MICRO_SHEET_COLS}
    for r in rows:
        for k, col in _MICRO_SHEET_COLS.items():
            try:
                totals[k] += float(r.get(col) or 0)
            except (ValueError, TypeError):
                pass
    return totals


# ── Gym Log ───────────────────────────────────────────────────────────────────

def log_gym(exercise: str, sets: int, reps: int, weight: float, rpe: float | None, notes: str = "", log_date: str = "", exercise_type: str = "strength", duration_min: int = 0, distance_km: float = 0, steps: int = 0):
    ws = _sheet(SHEET_GYM)
    now = datetime.now()
    row_date = log_date or now.strftime("%Y-%m-%d")
    row_time = now.strftime("%H:%M") if not log_date else ""
    ws.append_row([
        row_date,
        row_time,
        exercise,
        sets,
        reps,
        weight,
        rpe if rpe else "",
        notes,
        exercise_type,            # col I — "strength", "cardio", or "steps"
        duration_min if duration_min else "",  # col J — minutes (cardio only)
        distance_km if distance_km else "",    # col K — km (runs only)
        steps if steps else "",               # col L — step count
    ])


def get_last_session(exercise: str) -> dict | None:
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    matches = [r for r in rows if str(r.get("Exercise", "")).lower() == exercise.lower()]
    if not matches:
        return None
    return matches[-1]


def get_pb(exercise: str) -> dict | None:
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    matches = [r for r in rows if str(r.get("Exercise", "")).lower() == exercise.lower()]
    if not matches:
        return None
    return max(matches, key=lambda r: float(r.get("Weight", 0)))


def get_today_gym() -> list[dict]:
    ws = _sheet(SHEET_GYM)
    today = _norm_date(date.today().isoformat())
    rows = _get_records(ws)
    return [r for r in rows if _norm_date(r.get("Date", "")) == today]


def get_gym_by_date(date_str: str) -> list[dict]:
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    target = _norm_date(date_str)
    return [r for r in rows if _norm_date(r.get("Date", "")) == target]


def get_sleep_by_date(date_str: str) -> dict | None:
    ws = _sheet(SHEET_SLEEP)
    rows = _get_records(ws)
    target = _norm_date(date_str)
    matches = [r for r in rows if _norm_date(r.get("Date", "")) == target]
    return matches[-1] if matches else None


# ── Sleep Log ─────────────────────────────────────────────────────────────────

def log_sleep(hours: float, notes: str = "", log_date: str = "",
              sleep_time: str = "", wake_time: str = ""):
    ws = _sheet(SHEET_SLEEP)
    row_date = log_date or date.today().strftime("%Y-%m-%d")
    # Columns: Date · Sleep Time · Wake Time · Hours · Notes
    ws.append_row([row_date, sleep_time, wake_time, hours, notes])


def get_sleep_streak() -> int:
    ws = _sheet(SHEET_SLEEP)
    rows = _get_records(ws)
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
    today = _norm_date(date.today().isoformat())
    rows = _get_records(ws)
    matches = [r for r in rows if _norm_date(r.get("Date", "")) == today]
    return matches[-1] if matches else None


def get_today_emotions() -> dict | None:
    """Return today's most recent emotions log row, or None."""
    ws = _sheet(SHEET_EMOTIONS)
    today = _norm_date(date.today().isoformat())
    rows = _get_records(ws)
    matches = [r for r in rows if _norm_date(r.get("Date", "")) == today]
    return matches[-1] if matches else None


def get_emotions_by_date(date_str: str) -> dict | None:
    """Return emotions log row for a specific date, or None."""
    ws = _sheet(SHEET_EMOTIONS)
    rows = _get_records(ws)
    target = _norm_date(date_str)
    matches = [r for r in rows if _norm_date(r.get("Date", "")) == target]
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
        # Body / weight columns (H–N)
        data.get("weight_start", ""),
        data.get("weight_end", ""),
        data.get("weight_change", ""),
        data.get("bf_start", ""),
        data.get("bf_end", ""),
        data.get("skeletal_muscle", ""),
        data.get("top_feel_tags", ""),
    ])


def get_week_food() -> list[dict]:
    from datetime import timedelta
    ws = _sheet(SHEET_FOOD)
    rows = _get_records(ws)
    today = date.today()
    week_start = _norm_date((today - timedelta(days=today.weekday())).isoformat())
    return [r for r in rows if _norm_date(r.get("Date", "")) >= week_start]


def _prev_week_range(offset: int = 1):
    """Return (prev_mon, prev_sun) strings for week offset. 1=last week, 2=week before."""
    from datetime import timedelta
    today = date.today()
    this_mon = today - timedelta(days=today.weekday())
    prev_mon = _norm_date((this_mon - timedelta(days=7 * offset)).isoformat())
    prev_sun = _norm_date((this_mon - timedelta(days=7 * (offset - 1) + 1)).isoformat())
    return prev_mon, prev_sun


def get_prev_week_food(offset: int = 1) -> list[dict]:
    """Return food rows for a previous Mon–Sun week. offset=1 last week, 2=week before."""
    ws = _sheet(SHEET_FOOD)
    rows = _get_records(ws)
    prev_mon, prev_sun = _prev_week_range(offset)
    return [r for r in rows if prev_mon <= _norm_date(r.get("Date", "")) <= prev_sun]


def get_prev_week_gym(offset: int = 1) -> list[dict]:
    """Return gym rows for a previous Mon–Sun week."""
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    prev_mon, prev_sun = _prev_week_range(offset)
    return [r for r in rows if prev_mon <= _norm_date(r.get("Date", "")) <= prev_sun]


def get_prev_week_body(offset: int = 1) -> list[dict]:
    """Return body log rows for a previous Mon–Sun week."""
    ws = _sheet(SHEET_BODY)
    rows = _get_records(ws)
    prev_mon, prev_sun = _prev_week_range(offset)
    return [r for r in rows if prev_mon <= _norm_date(r.get("Date", "")) <= prev_sun]


def get_prev_week_sleep(offset: int = 1) -> list[dict]:
    """Return sleep rows for a previous Mon–Sun week."""
    ws = _sheet(SHEET_SLEEP)
    rows = _get_records(ws)
    prev_mon, prev_sun = _prev_week_range(offset)
    return [r for r in rows if prev_mon <= _norm_date(r.get("Date", "")) <= prev_sun]


def get_week_sleep() -> list[dict]:
    """Return sleep rows for the current Mon–Sun week."""
    from datetime import timedelta
    ws = _sheet(SHEET_SLEEP)
    rows = _get_records(ws)
    today = date.today()
    week_start = _norm_date((today - timedelta(days=today.weekday())).isoformat())
    return [r for r in rows if _norm_date(r.get("Date", "")) >= week_start]


def get_strength_exercises_past_weeks(weeks: int = 3) -> list[dict]:
    """Return unique strength exercises from the last N weeks, most recent weight per exercise.

    Returns list of dicts: {exercise, muscle_group, last_weight, last_date}
    Ordered by muscle group then exercise name.
    """
    from datetime import timedelta
    ws_gym = _sheet(SHEET_GYM)
    rows = _get_records(ws_gym)
    cutoff = _norm_date((date.today() - timedelta(weeks=weeks)).isoformat())

    # Collect all strength rows in window
    recent = [
        r for r in rows
        if _norm_date(r.get("Date", "")) >= cutoff
        and str(r.get("Type", "strength")).lower() != "cardio"
        and r.get("Exercise")
    ]

    # Per exercise: take the most recent row
    seen: dict[str, dict] = {}
    for r in recent:
        ex = str(r.get("Exercise", "")).strip()
        if not ex:
            continue
        prev = seen.get(ex)
        if prev is None or _norm_date(r.get("Date", "")) > _norm_date(prev.get("Date", "")):
            seen[ex] = r

    # Enrich with muscle group from catalogue
    try:
        cat_rows = get_exercise_catalogue()
        cat_map = {str(r.get("Exercise Name", "")).lower(): r for r in cat_rows}
    except Exception:
        cat_map = {}

    result = []
    for ex, r in seen.items():
        cat = cat_map.get(ex.lower(), {})
        muscle = str(cat.get("Muscle Group", "") or r.get("Notes", "")).strip()
        w = r.get("Weight", "") or r.get("weight_kg", "")
        try:
            w = float(w) if w else 0
        except (ValueError, TypeError):
            w = 0
        result.append({
            "exercise": ex,
            "muscle_group": muscle,
            "last_weight": w,
            "last_date": r.get("Date", ""),
        })

    # Sort by muscle group then name
    result.sort(key=lambda x: (x["muscle_group"].lower(), x["exercise"].lower()))
    return result


def get_week_gym_days() -> int:
    """Return number of unique days with a strength session this week."""
    rows = get_week_gym()
    return len({
        _norm_date(r.get("Date", ""))
        for r in rows
        if r.get("Date") and str(r.get("Type", "strength")).lower() != "cardio"
    })


def get_week_cardio_sessions(min_minutes: int = 20) -> int:
    """Return number of days with cardio ≥ min_minutes OR ≥ 10k steps this week."""
    import re as _re
    from collections import defaultdict
    _dur_re = _re.compile(r'(\d+)\s*min', _re.IGNORECASE)
    rows = get_week_gym()
    day_duration: dict[str, int] = defaultdict(int)
    day_steps: dict[str, int] = defaultdict(int)
    for r in rows:
        row_type = str(r.get("Type", "")).lower()
        date_key = _norm_date(r.get("Date", ""))
        if row_type == "cardio":
            try:
                dur = int(r.get("Duration (min)", 0) or 0)
                if dur == 0:
                    m = _dur_re.search(str(r.get("Notes", "")))
                    if m:
                        dur = int(m.group(1))
                day_duration[date_key] += dur
            except (ValueError, TypeError):
                pass
        elif row_type == "steps":
            try:
                s = int(str(r.get("Steps", "") or r.get("Notes", "") or "0").replace(",", "").split()[0])
                day_steps[date_key] = max(day_steps[date_key], s)
            except (ValueError, TypeError):
                pass
    all_days = set(day_duration) | set(day_steps)
    return sum(
        1 for d in all_days
        if day_duration.get(d, 0) >= min_minutes or day_steps.get(d, 0) >= 10000
    )


def get_week_gym() -> list[dict]:
    from datetime import timedelta
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    today = date.today()
    week_start = _norm_date((today - timedelta(days=today.weekday())).isoformat())
    return [r for r in rows if _norm_date(r.get("Date", "")) >= week_start]


# ── Body Log ──────────────────────────────────────────────────────────────────

def log_body(
    weight_kg: float | None,
    body_fat_pct: float | None,
    tags: list[str],
    notes: str = "",
    lean_mass_kg: float | None = None,
    skeletal_muscle_kg: float | None = None,
    fat_mass_kg: float | None = None,
    visceral_fat_level: int | None = None,
):
    """Log a body check-in. All fields except tags are optional."""
    ws = _sheet(SHEET_BODY)
    now = datetime.now()
    cycle_day, phase = get_cycle_info()

    bmi = round(weight_kg / (HEIGHT_M ** 2), 1) if weight_kg else ""

    # Lean mass: use provided value, or derive from weight + BF%
    if lean_mass_kg:
        lean_mass = lean_mass_kg
    elif weight_kg and body_fat_pct:
        lean_mass = round(weight_kg * (1 - body_fat_pct / 100), 1)
    else:
        lean_mass = ""

    ws.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        weight_kg if weight_kg else "",
        bmi,
        body_fat_pct if body_fat_pct else "",
        lean_mass,
        skeletal_muscle_kg if skeletal_muscle_kg else "",
        fat_mass_kg if fat_mass_kg else "",
        visceral_fat_level if visceral_fat_level else "",
        ", ".join(tags) if tags else "",
        notes,
        cycle_day or "",
        phase or "",
    ])


def get_week_body() -> list[dict]:
    """Return body log rows for the current week (Mon–Sun)."""
    from datetime import timedelta
    ws = _sheet(SHEET_BODY)
    rows = _get_records(ws)
    today = date.today()
    week_start = _norm_date((today - timedelta(days=today.weekday())).isoformat())
    return [r for r in rows if _norm_date(r.get("Date", "")) >= week_start]


def get_body_trend(days: int = 7) -> list[dict]:
    """Return last N days of body log rows."""
    from datetime import timedelta
    ws = _sheet(SHEET_BODY)
    rows = _get_records(ws)
    cutoff = _norm_date((date.today() - timedelta(days=days)).isoformat())
    return [r for r in rows if _norm_date(r.get("Date", "")) >= cutoff]


def get_body_by_date(date_str: str) -> dict | None:
    """Return body log row for a specific date, or None."""
    ws = _sheet(SHEET_BODY)
    rows = _get_records(ws)
    target = _norm_date(date_str)
    matches = [r for r in rows if _norm_date(r.get("Date", "")) == target]
    return matches[-1] if matches else None


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


# ── Cycle tracking ────────────────────────────────────────────────────────────

def get_last_period_start() -> date | None:
    """Return the most recent period start date from Cycle Log."""
    try:
        ws = _sheet(SHEET_CYCLE)
        rows = _get_records(ws)
        day1_rows = [r for r in rows if str(r.get("Cycle Day", "")).strip() == "1"]
        if not day1_rows:
            return None
        latest = max(day1_rows, key=lambda r: r.get("Date", ""))
        d = latest.get("Date", "")
        return date.fromisoformat(str(d)) if d else None
    except Exception:
        return None


def get_cycle_info() -> tuple[int | None, str | None]:
    """Return (cycle_day, phase) based on last period start. Never shown to user."""
    start = get_last_period_start()
    if not start:
        return None, None
    today = date.today()
    cycle_day = (today - start).days + 1
    if cycle_day <= 5:
        phase = "menstrual"
    elif cycle_day <= 13:
        phase = "follicular"
    elif cycle_day == 14:
        phase = "ovulatory"
    elif cycle_day <= 28:
        phase = "luteal"
    else:
        phase = "late luteal"
    return cycle_day, phase


def log_period_start(symptoms: str = "", notes: str = ""):
    ws = _sheet(SHEET_CYCLE)
    today = date.today().strftime("%Y-%m-%d")
    ws.append_row([today, 1, "menstrual", symptoms, "started", notes])


def log_emotions(mood: int, energy: int, notes: str = "", log_date: str = ""):
    ws = _sheet(SHEET_EMOTIONS)
    cycle_day, phase = get_cycle_info()
    now = datetime.now()
    row_date = log_date or now.strftime("%Y-%m-%d")
    row_time = now.strftime("%H:%M") if not log_date else ""
    ws.append_row([
        row_date,
        row_time,
        mood,
        energy,
        notes,
        cycle_day or "",
        phase or "",
    ])


def log_activity(activity_type: str, duration_mins: int, notes: str = ""):
    ws = _sheet(SHEET_ACTIVITY)
    cycle_day, phase = get_cycle_info()
    today = date.today()
    ws.append_row([
        today.strftime("%Y-%m-%d"),
        activity_type,
        duration_mins,
        notes,
        cycle_day or "",
        phase or "",
    ])


# ── Cycle summary data ────────────────────────────────────────────────────────

def get_cycle_summary_data() -> dict:
    """Aggregate data for a full cycle summary report."""
    start = get_last_period_start()
    if not start:
        return {}
    from datetime import timedelta
    prev_start = None
    try:
        ws = _sheet(SHEET_CYCLE)
        rows = _get_records(ws)
        day1_rows = sorted(
            [r for r in rows if str(r.get("Cycle Day", "")).strip() == "1"],
            key=lambda r: r.get("Date", ""),
        )
        if len(day1_rows) >= 2:
            prev_start = date.fromisoformat(str(day1_rows[-2]["Date"]))
    except Exception:
        pass

    cycle_start = prev_start or (start - timedelta(days=28))
    cycle_end = start

    def in_cycle(r):
        d = str(r.get("Date", ""))
        return d >= cycle_start.strftime("%Y-%m-%d") and d < cycle_end.strftime("%Y-%m-%d")

    emotions = [r for r in _get_records(_sheet(SHEET_EMOTIONS)) if in_cycle(r)]
    activities = [r for r in _get_records(_sheet(SHEET_ACTIVITY)) if in_cycle(r)]
    gym = [r for r in _get_records(_sheet(SHEET_GYM)) if in_cycle(r)]
    food = [r for r in _get_records(_sheet(SHEET_FOOD)) if in_cycle(r)]

    # Mood by phase
    mood_by_phase: dict = {}
    for r in emotions:
        p = str(r.get("Phase", "unknown"))
        mood_by_phase.setdefault(p, []).append(int(r.get("Mood (1-10)", 5)))

    avg_mood = {p: round(sum(v) / len(v), 1) for p, v in mood_by_phase.items()}

    return {
        "cycle_start": cycle_start.strftime("%Y-%m-%d"),
        "cycle_end": cycle_end.strftime("%Y-%m-%d"),
        "avg_mood_by_phase": avg_mood,
        "total_gym_sessions": len({r.get("Date") for r in gym}),
        "total_activities": len(activities),
        "avg_calories": round(sum(int(r.get("Calories", 0)) for r in food) / max(len({r.get("Date") for r in food}), 1)),
        "avg_protein": round(sum(float(r.get("Protein", 0)) for r in food) / max(len({r.get("Date") for r in food}), 1), 1),
    }


# ── Exercise Catalogue ────────────────────────────────────────────────────────

def get_exercise_catalogue() -> list[dict]:
    """Return all rows from Exercise Catalogue."""
    ws = _sheet(SHEET_CATALOGUE)
    return _get_records(ws)


def get_exercises_by_set(set_name: str) -> list[dict]:
    rows = get_exercise_catalogue()
    return [r for r in rows if str(r.get("Set", "")).lower() == set_name.lower()]


def get_optional_exercises() -> list[dict]:
    rows = get_exercise_catalogue()
    return [r for r in rows if str(r.get("Set", "")).lower() == "optional"]


def get_exercises_by_muscle(muscle_group: str) -> list[dict]:
    rows = get_exercise_catalogue()
    return [
        r for r in rows
        if muscle_group.lower() in str(r.get("Muscle Group", "")).lower()
    ]


def get_available_sets() -> list[str]:
    rows = get_exercise_catalogue()
    sets = {str(r.get("Set", "")) for r in rows if str(r.get("Set", "")).lower() != "optional"}
    return sorted(sets)


def add_exercise_to_catalogue(
    name: str,
    muscle_group: str,
    set_name: str = "optional",
    sets: int = 3,
    notes: str = "",
) -> bool:
    """Add a new exercise. Returns False if already exists."""
    existing = get_exercise_catalogue()
    for r in existing:
        if str(r.get("Exercise Name", "")).lower() == name.lower():
            return False
    ws = _sheet(SHEET_CATALOGUE)
    ws.append_row([name, muscle_group, set_name, sets, "", "", notes])
    return True


def update_exercise_set(name: str, set_name: str):
    """Move an exercise to a different set."""
    ws = _sheet(SHEET_CATALOGUE)
    rows = _get_records(ws)
    for i, r in enumerate(rows, start=2):  # row 1 = header
        if str(r.get("Exercise Name", "")).lower() == name.lower():
            ws.update_cell(i, 3, set_name)  # col C = Set
            return True
    return False


def update_exercise_weight(name: str, weight: float):
    """Update last weight and last used date after a session."""
    ws = _sheet(SHEET_CATALOGUE)
    rows = _get_records(ws)
    today = date.today().strftime("%Y-%m-%d")
    for i, r in enumerate(rows, start=2):
        if str(r.get("Exercise Name", "")).lower() == name.lower():
            ws.update_cell(i, 5, weight)   # col E = Last Weight
            ws.update_cell(i, 6, today)    # col F = Last Used
            return True
    return False


def create_new_set(set_name: str, exercises: list[dict]):
    """Bulk-add exercises for a new named set."""
    ws = _sheet(SHEET_CATALOGUE)
    for ex in exercises:
        ws.append_row([
            ex.get("name", ""),
            ex.get("muscle_group", ""),
            set_name,
            ex.get("sets", 3),
            "",
            "",
            ex.get("notes", ""),
        ])


def get_last_two_weeks_weight(exercise_name: str) -> list[dict]:
    """Return last 2 weeks of gym log rows for this exercise."""
    from datetime import timedelta
    ws = _sheet(SHEET_GYM)
    rows = _get_records(ws)
    cutoff = (date.today() - timedelta(days=14)).strftime("%Y-%m-%d")
    return [
        r for r in rows
        if str(r.get("Exercise", "")).lower() == exercise_name.lower()
        and str(r.get("Date", "")) >= cutoff
    ]


def parse_exercise_list(doc_text: str) -> list:
    """Extract numbered exercises from CoachTraining doc text."""
    import re
    exercises = []
    for line in doc_text.split("\n"):
        match = re.match(r"^\s*(\d+)\.\s+(.+)", line)
        if match:
            exercises.append(match.group(2).strip())
    return exercises


def get_coach_nutrition() -> str:
    return _read_doc(COACH_NUTRITION_DOC_ID)


def get_coach_training() -> str:
    return _read_doc(COACH_TRAINING_DOC_ID)


def get_weekly_goals() -> str:
    return _read_doc(WEEKLY_GOALS_DOC_ID)


# ── Content Log ───────────────────────────────────────────────────────────────

def log_content(raw_note: str, week_num: str, pillar: str, angle: str, suggested_angle: str, log_date: str = ""):
    """Log a content idea/thought.
    Columns: Date · Time · Week # · Pillar · Angle · Raw Note · Suggested Angle
    """
    ws = _sheet(SHEET_CONTENT)
    now = datetime.now()
    row_date = log_date or now.strftime("%Y-%m-%d")
    row_time = now.strftime("%H:%M")
    ws.append_row([row_date, row_time, week_num, pillar, angle, raw_note, suggested_angle])


def get_content_log(limit: int = 50) -> list[dict]:
    """Return the most recent content log entries."""
    ws = _sheet(SHEET_CONTENT)
    rows = _get_records(ws)
    return rows[-limit:] if len(rows) > limit else rows


def log_reflection(note: str, log_date: str = "") -> None:
    """Append a do-better / reflection note to Content Log (Pillar=Reflection)."""
    from datetime import date as _d
    transform_start = _d(2026, 5, 18)
    today = _d.today()
    days = (today - transform_start).days
    week_num = f"Week {max(1, days // 7 + 1)}"
    log_content(note, week_num, "Reflection", "Do Better", "", log_date)


def get_micros_by_date(date_str: str) -> list[dict]:
    """Return Food Log rows with micro columns for a given date."""
    return get_food_by_date(date_str)


def get_today_micros() -> list[dict]:
    return get_today_food()


def get_week_micros() -> list[dict]:
    return get_week_food()


def log_report(report_type: str, date_range: str, output: str) -> None:
    """Store a bot report output: type, date/range, and full text."""
    ws = _sheet(SHEET_REPORTS)
    now = datetime.now()
    ws.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        report_type,
        date_range,
        output,
    ])
