"""All Claude API calls — Buff Buddy voice lives here."""
import base64
import json
import anthropic
from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Buff Buddy system prompt ───────────────────────────────────────────────────

BUFF_BUDDY_SYSTEM = """You are Buff Buddy — a drill sergeant with heart. Sports locker room energy. Firm, direct, and genuinely invested in Liz's progress — not just her reps.

USER'S NAME RULES:
- "Liz" → default, everyday use
- "Lizzard" → PRs and big wins only
- "Lizzy" → soft moments only: struggling, low mood, cycle lows

TONE BY ENTRY TYPE:
- Gym: locker room hype, celebratory, punchy. Always reference actual numbers. Compare to last session when data exists.
- Meal: matter of fact, no fluff. State macros clearly. No judgment ever.
- Recovery/Sleep: calm and steady. Acknowledge rest is part of the quest. Never catastrophise short sleep.
- Emotions/Cycle: warm, holds space, non-clinical. Never force positivity. If cycle data exists, connect mood to phase naturally.

QUEST LANGUAGE — use sparingly, max one per message, only when it fits:
"Mission logged" — after any completed entry
"Quest continues" — end of day / summary
"Levelled up" — PR or new personal best
"Boss battle" — hard workout ahead
"Fuelled" — meal acknowledged
"Recovery mission" — rest day or sleep entry
"Game time" — starting a gym session
"The scoreboard" — referring to daily summary

VOICE RULES:
- Short sentences always. One idea per sentence. Max 3 lines per reply.
- Affirmations must reference actual logged data — never generic.
- Read the room — hype when deserved, steady when she's struggling.
- Firm not harsh — hold her accountable but never shame her.
- Quirky not try-hard — quest language must feel natural, not forced.

NEVER SAY:
- "Great job!" or "Well done!" with no context
- "I'm just an AI, but..."
- "You should consider..."
- "Remember to stay hydrated!" unprompted
- Toxic positivity on hard days
- More than one quest phrase per message"""


def _call(prompt: str, max_tokens: int = 300, system: str = BUFF_BUDDY_SYSTEM) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ── Food ──────────────────────────────────────────────────────────────────────

def analyse_food_photo(image_bytes: bytes, mime_type: str = "image/jpeg", caption_hint: str = "", meal_history: list[str] | None = None) -> dict:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    hint_line = f"\nUser description: {caption_hint}" if caption_hint else ""
    history_line = ""
    if meal_history:
        history_line = "\n\nThis user's recent meals for reference — use to infer their usual cooking style, portions, and preferences:\n" + "\n".join(f"- {m}" for m in meal_history)
    prompt = (
        "Analyse this meal photo and estimate macros per item. "
        "The user's description takes priority over what you see. "
        "Use their meal history to infer cooking style and portions (e.g. if they always have half-boiled eggs, don't guess fried). "
        "Include EVERY item visible or mentioned, even near-zero macro items."
        f"{hint_line}{history_line}\n\n"
        "Reply in this exact JSON format, nothing else:\n"
        "{\n"
        '  "meal_type": "breakfast|lunch|dinner|snack|supper|NONE",\n'
        '  "note": "one-line Buff Buddy style response",\n'
        '  "items": [\n'
        '    {"name": "item name", "calories": 0, "protein": 0.0, "carbs": 0.0, "fats": 0.0}\n'
        "  ]\n"
        "}"
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=BUFF_BUDDY_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_itemised_response(response.content[0].text)


def analyse_food_text(text: str, meal_history: list[str] | None = None) -> dict:
    if meal_history:
        # Use the most recent meal as a structured lookup table.
        # Split it into individual items so Claude has explicit portion lines to copy.
        most_recent = meal_history[0]
        prev_items = [item.strip() for item in most_recent.split(", ") if item.strip()]
        items_block = "\n".join(f"  - {item}" for item in prev_items)

        prompt = (
            "MEAL LOGGING — HISTORY MATCHING\n\n"
            "User's confirmed portion sizes from their last logged meal of this type:\n"
            f"{items_block}\n\n"
            f"New meal to log: \"{text}\"\n\n"
            "Step 1: For each food word in the new meal, find the matching item in the list above.\n"
            "Step 2: Use the EXACT item name (including quantity) from the list — do not change it.\n"
            "Step 3: Only use a different quantity if the user explicitly states one (e.g. 'THREE eggs').\n"
            "Step 4: For any item not in the history list, estimate normally.\n\n"
            "Matching rules:\n"
            "  'egg' / 'eggs' → match any 'egg' item in history, copy it exactly\n"
            "  'bread' / 'sandwich' / 'toast' → match any 'bread' or 'toast' item in history\n"
            "  'pbb' / 'peanut butter' / 'pb' → match any 'peanut butter' item in history\n"
            "  'coffee' → match any 'coffee' item in history\n"
            "  'oats' / 'oatmeal' → match any 'oat' item in history\n\n"
            "Include ALL items, even near-zero (black coffee = ~5 cal).\n"
            "sugar = total sugars in grams (eggs/meat/veg ≈ 0, bread/fruit/dairy = some).\n\n"
            "Reply in this exact JSON format only:\n"
            "{\n"
            '  "meal_type": "breakfast|lunch|dinner|snack|supper|NONE",\n'
            '  "note": "one-line Buff Buddy style response",\n'
            '  "items": [\n'
            '    {"name": "<exact item name from history or new item>", "calories": 0, "protein": 0.0, "carbs": 0.0, "fats": 0.0, "sugar": 0.0}\n'
            "  ]\n"
            "}"
        )
    else:
        prompt = (
            f"Log this meal: {text}\n\n"
            "Include ALL items, even near-zero (black coffee = ~5 cal).\n"
            "sugar = total sugars in grams.\n\n"
            "Reply in this exact JSON format only:\n"
            "{\n"
            '  "meal_type": "breakfast|lunch|dinner|snack|supper|NONE",\n'
            '  "note": "one-line Buff Buddy style response",\n'
            '  "items": [\n'
            '    {"name": "item name", "calories": 0, "protein": 0.0, "carbs": 0.0, "fats": 0.0, "sugar": 0.0}\n'
            "  ]\n"
            "}"
        )
    return _parse_itemised_response(_call(prompt, max_tokens=600))


def _parse_itemised_response(text: str) -> dict:
    """Parse itemised JSON response into a standard macro dict with items list."""
    start = text.find("{")
    end = text.rfind("}") + 1
    try:
        data = json.loads(text[start:end])
    except Exception:
        # Fallback to old format
        return _parse_macro_response(text)

    items = data.get("items", [])
    raw_type = data.get("meal_type", "NONE").strip().lower()
    meal_type = raw_type if raw_type in {"breakfast", "lunch", "dinner", "snack", "supper"} else ""

    total_cal = sum(int(i.get("calories", 0)) for i in items)
    total_pro = sum(float(i.get("protein", 0)) for i in items)
    total_carb = sum(float(i.get("carbs", 0)) for i in items)
    total_fat = sum(float(i.get("fats", 0)) for i in items)
    total_sugar = sum(float(i.get("sugar", 0)) for i in items)
    description = ", ".join(i["name"] for i in items if i.get("name"))

    return {
        "description": description,
        "meal_type": meal_type,
        "calories": total_cal,
        "protein": round(total_pro, 1),
        "carbs": round(total_carb, 1),
        "fats": round(total_fat, 1),
        "sugar": round(total_sugar, 1),
        "confidence": "medium",
        "note": data.get("note", ""),
        "items": items,
    }


def _parse_macro_response(text: str) -> dict:
    result = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower()] = val.strip()
    raw_type = result.get("meal_type", "NONE").strip().lower()
    meal_type = raw_type if raw_type in {"breakfast", "lunch", "dinner", "snack", "supper"} else ""
    return {
        "description": result.get("description", "Unknown meal"),
        "meal_type": meal_type,
        "calories": int(result.get("calories", 0)),
        "protein": float(result.get("protein", 0)),
        "carbs": float(result.get("carbs", 0)),
        "fats": float(result.get("fats", 0)),
        "confidence": result.get("confidence", "medium"),
        "note": result.get("note", ""),
    }


def generate_food_description(meal_desc: str, calories: int, protein: float) -> str:
    """One-sentence MasterChef/Ratatouille-style vivid food description."""
    prompt = (
        f"One sentence only. MasterChef commentary — vivid, sensory, direct. "
        f"Notice what makes this meal interesting: texture, contrast, purpose. Not precious, not generic.\n\n"
        f"Meal: {meal_desc}\n"
        f"Macros: {calories} cal, {protein:.0f}g protein\n\n"
        "No quotes. No labels. One sentence."
    )
    return _call(prompt, max_tokens=120, system=BUFF_BUDDY_SYSTEM)


# ── Gym ───────────────────────────────────────────────────────────────────────

def parse_gym_entry(text: str) -> dict:
    prompt = (
        "Parse this gym log entry. Reply in this exact format, nothing else:\n"
        "EXERCISE: <exercise name, title case>\n"
        "SETS: <integer>\n"
        "REPS: <integer>\n"
        "WEIGHT: <kg as decimal, 0 if bodyweight>\n"
        "RPE: <decimal 1-10, or NONE>\n"
        "NOTES: <any extra detail, or empty>\n\n"
        f"Entry: {text}"
    )
    raw = _call(prompt, max_tokens=200)
    result = {}
    for line in raw.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower()] = val.strip()
    return {
        "exercise": result.get("exercise", "Unknown"),
        "sets": int(result.get("sets", 0)),
        "reps": int(result.get("reps", 0)),
        "weight": float(result.get("weight", 0)),
        "rpe": None if result.get("rpe", "NONE").upper() == "NONE" else float(result.get("rpe", 0)),
        "notes": result.get("notes", ""),
    }


def parse_session_results(exercise_list: list, user_input: str) -> list:
    exercises_str = "\n".join(f"{i+1}. {ex}" for i, ex in enumerate(exercise_list))
    prompt = (
        f"Exercise list:\n{exercises_str}\n\n"
        f"User logged:\n{user_input}\n\n"
        "Match input to exercise list. Reply as JSON array only:\n"
        '[{"number":1,"exercise":"name","weight_kg":0,"sets":0,"reps":0,"rpe":null,"skipped":false,"notes":"","type":"strength","duration_min":0,"distance_km":0}]\n\n'
        "Rules:\n"
        "- ONLY include exercises the user explicitly mentioned. If an exercise from the list is NOT mentioned by the user, set skipped:true.\n"
        "- Default sets=3 if the user mentions an exercise but doesn't specify sets.\n"
        "- weight_kg=0 for bodyweight exercises. reps=0 if not specified.\n"
        "- For cardio (treadmill, stairmaster, stair master, cycling, rowing, elliptical, running, walking, HIIT, bike): "
        "set type=cardio, sets=0, reps=0, weight_kg=0. ALWAYS extract duration_min from the user's text (e.g. '20 min', '30 minutes', 'Level 5 20min' → duration_min=20). Never leave duration_min=0 for cardio.\n"
        "- For runs: also extract distance_km (e.g. '5km run', 'ran 5k' → distance_km=5.0). If no distance given, distance_km=0.\n"
        "- Put the full user description (e.g. '5km run, 28min') in the notes field for cardio entries.\n"
        "- For strength: type=strength, duration_min=0.\n"
        "- Also include any cardio mentioned that is NOT in the exercise list — add it as an extra entry with number=0."
    )
    text = _call(prompt, max_tokens=800)
    start = text.find("[")
    end = text.rfind("]") + 1
    return json.loads(text[start:end])


def gym_session_reply(session_lines: list, has_pr: bool) -> str:
    summary = "\n".join(session_lines)
    prompt = (
        f"Session data:\n{summary}\n\n"
        f"PR achieved: {has_pr}\n\n"
        "Give a Buff Buddy reply for this completed gym session. Max 3 lines."
    )
    return _call(prompt, max_tokens=150)


def generate_food_day_story(meals: str, cal: int, target_cal: int, protein: float, target_protein: float) -> str:
    """One fun creative sentence describing everything Liz ate today."""
    prompt = (
        f"Meals logged today: {meals}\n"
        f"Calories: {cal}/{target_cal}, Protein: {protein:.0f}g/{target_protein:.0f}g\n\n"
        "Write ONE fun, creative sentence (max 20 words) describing everything Liz put in her body today. "
        "Be playful and specific — name the actual foods, give it personality. No emojis. No generic phrases."
    )
    return _call(prompt, max_tokens=80)


def generate_end_of_day_coaching(day_summary: str, week_summary: str, yesterday_summary: str = "") -> str:
    """Compact coach push for end-of-day — acknowledge, compare, push."""
    yesterday_block = f"Yesterday: {yesterday_summary}\n" if yesterday_summary else ""
    prompt = (
        f"Today: {day_summary}\n"
        f"{yesterday_block}"
        f"Week: {week_summary}\n\n"
        "Write a Buff Buddy coach message. 3–4 lines max. "
        "Acknowledge one specific thing from today (win OR gap). "
        "If yesterday data exists, call out one change (better or worse) in one line. "
        "End with one sharp, specific action for tomorrow or the remaining days. "
        "Sound like a real coach — direct, warm, no fluff."
    )
    return _call(prompt, max_tokens=220)


def generate_daily_summary_note(context: str, missing: list[str], yesterday_context: str = "") -> str:
    """Evening coaching note — references what was logged, compares to yesterday, asks about missing items."""
    missing_str = ", ".join(missing) if missing else "nothing"
    yesterday_block = f"\nYesterday's numbers:\n{yesterday_context}\n" if yesterday_context else ""
    prompt = (
        f"Today's log:\n{context}\n"
        f"{yesterday_block}\n"
        f"Not logged today: {missing_str}\n\n"
        "Write a Buff Buddy evening check-in note. Max 4 lines.\n"
        "- If yesterday data exists: start with one sharp comparison — what improved, what didn't (be specific with numbers).\n"
        "- Call out one win or concern from today.\n"
        "- If anything is missing, ask about exactly one of them naturally.\n"
        "- End with one priority for tomorrow."
    )
    return _call(prompt, max_tokens=250)


# ── Date extraction ───────────────────────────────────────────────────────────

def extract_log_date(text: str) -> str | None:
    """Return ISO date string if message refers to a past day, else None (= today)."""
    import re
    from datetime import date, timedelta
    today = date.today()
    lower = text.lower()

    # Fast-path regex for common patterns
    if re.search(r"\byesterday|\blast night\b|\blast evening\b|\byest\b", lower):
        return (today - timedelta(days=1)).isoformat()
    if re.search(r"\b2 days ago\b|\btwo days ago\b", lower):
        return (today - timedelta(days=2)).isoformat()
    if re.search(r"\b3 days ago\b|\bthree days ago\b", lower):
        return (today - timedelta(days=3)).isoformat()

    # Day-name references — ask Claude
    if re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|last \w+)\b", lower):
        prompt = (
            f"Today is {today.strftime('%Y-%m-%d')} ({today.strftime('%A')}).\n"
            "What past date does this message refer to? Reply with YYYY-MM-DD only, or 'today'.\n\n"
            f"Message: {text}"
        )
        raw = _call(prompt, max_tokens=15).strip().lower()
        if raw == "today" or not raw:
            return None
        try:
            d = date.fromisoformat(raw)
            return raw if d < today else None
        except ValueError:
            return None

    return None


# ── Recovery / Sleep ──────────────────────────────────────────────────────────

def parse_sleep(text: str) -> dict:
    """Parse natural sleep description into hours, sleep/wake times and notes."""
    prompt = (
        "Parse this sleep description and return JSON only:\n"
        '{"hours": <total sleep hours as float>, "sleep_time": "<HH:MM 24h or empty>", '
        '"wake_time": "<HH:MM 24h or empty>", "notes": "<everything else mentioned>"}\n\n'
        "Rules:\n"
        "- Calculate TOTAL sleep from times if given (e.g. '11pm to 7am' → hours=8.0, sleep_time='23:00', wake_time='07:00')\n"
        "- '11:30pm' → '23:30', '12:30am' → '00:30', '7am' → '07:00'\n"
        "- If only hours given (e.g. '7.5h'), set hours=7.5, leave sleep_time/wake_time empty\n"
        "- notes: capture everything else — quality, dreams, interruptions, restlessness\n\n"
        f"Message: {text}\n\n"
        "Reply with JSON only."
    )
    raw = _call(prompt, max_tokens=120)
    start, end = raw.find("{"), raw.rfind("}") + 1
    try:
        data = json.loads(raw[start:end])
        return {
            "hours": float(data.get("hours", 6)),
            "sleep_time": data.get("sleep_time", ""),
            "wake_time": data.get("wake_time", ""),
            "notes": data.get("notes", ""),
        }
    except Exception:
        return {"hours": 6.0, "sleep_time": "", "wake_time": "", "notes": text}


def recovery_reply(hours: float, notes: str, streak: int) -> str:
    prompt = (
        f"Sleep: {hours}h. Notes: {notes or 'none'}. Streak: {streak} nights of 7h+.\n\n"
        "Give a Buff Buddy recovery reply. Max 2 lines. Calm and steady tone. "
        "Reference what they mentioned (dreams, meditation, interruptions etc) if noted."
    )
    return _call(prompt, max_tokens=100)


# ── Emotions ──────────────────────────────────────────────────────────────────

def parse_emotions(text: str) -> dict:
    """Extract mood score, energy score, and notes from free text."""
    prompt = (
        "Extract mood and energy from this message. Reply as JSON only:\n"
        '{"mood": <1-10>, "energy": <1-10>, "notes": "<brief summary>"}\n\n'
        "Rules: 1=very low, 10=excellent. Infer from context. If unclear use 5.\n\n"
        f"Message: {text}"
    )
    raw = _call(prompt, max_tokens=100)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return {"mood": 5, "energy": 5, "notes": text[:100]}


def emotions_reply(mood: int, energy: int, notes: str, cycle_day: int | None, phase: str | None) -> str:
    cycle_ctx = f"Cycle day {cycle_day}, phase: {phase}." if cycle_day else "No cycle data."
    prompt = (
        f"Mood: {mood}/10, Energy: {energy}/10\n"
        f"Notes: {notes}\n"
        f"Cycle: {cycle_ctx}\n\n"
        "Give a Buff Buddy emotions reply. Warm, non-clinical. Max 3 lines. "
        "If cycle data exists, connect mood to phase naturally. Use 'Lizzy' only if she's struggling."
    )
    return _call(prompt, max_tokens=150)


# ── Body check-in ─────────────────────────────────────────────────────────────

BODY_TAGS = [
    "lethargic", "strong", "tired", "stressed", "not enough sleep",
    "bloated", "sore", "energised", "brain fog", "good mood",
]

def parse_body_checkin(text: str) -> dict:
    """Extract weight, body composition metrics, feel tags, and notes from a check-in."""
    tags_list = ", ".join(BODY_TAGS)
    prompt = (
        "Parse this body check-in message. Reply as JSON only:\n"
        '{"weight_kg": <float or null>, "body_fat_pct": <float or null>, '
        '"lean_mass_kg": <float or null>, "skeletal_muscle_kg": <float or null>, '
        '"fat_mass_kg": <float or null>, "visceral_fat_level": <int or null>, '
        '"tags": ["<tag1>", ...], "notes": "<any extra detail or empty>"}\n\n'
        f"Available tags (pick all that apply, exact spelling): {tags_list}\n\n"
        "Rules:\n"
        "- weight_kg: total body weight in kg. null if not mentioned.\n"
        "- body_fat_pct: body fat percentage (e.g. '27.8%' → 27.8). null if not mentioned.\n"
        "- lean_mass_kg: lean body mass in kg. null if not mentioned.\n"
        "- skeletal_muscle_kg: skeletal muscle mass in kg. null if not mentioned.\n"
        "- fat_mass_kg: total fat mass in kg. null if not mentioned.\n"
        "- visceral_fat_level: visceral fat level as integer (e.g. 'visceral fat 6' → 6). null if not.\n"
        "- tags: match feelings to available tags. 'groggy'→lethargic+not enough sleep. 'stiff'→sore.\n"
        "- notes: anything else that doesn't fit the above fields.\n\n"
        f"Message: {text}"
    )
    raw = _call(prompt, max_tokens=200)
    start, end = raw.find("{"), raw.rfind("}") + 1
    try:
        data = json.loads(raw[start:end])
        return {
            "weight_kg": float(data["weight_kg"]) if data.get("weight_kg") else None,
            "body_fat_pct": float(data["body_fat_pct"]) if data.get("body_fat_pct") else None,
            "lean_mass_kg": float(data["lean_mass_kg"]) if data.get("lean_mass_kg") else None,
            "skeletal_muscle_kg": float(data["skeletal_muscle_kg"]) if data.get("skeletal_muscle_kg") else None,
            "fat_mass_kg": float(data["fat_mass_kg"]) if data.get("fat_mass_kg") else None,
            "visceral_fat_level": int(data["visceral_fat_level"]) if data.get("visceral_fat_level") else None,
            "tags": [t for t in (data.get("tags") or []) if t in BODY_TAGS],
            "notes": data.get("notes", ""),
        }
    except Exception:
        return {
            "weight_kg": None, "body_fat_pct": None, "lean_mass_kg": None,
            "skeletal_muscle_kg": None, "fat_mass_kg": None, "visceral_fat_level": None,
            "tags": [], "notes": text[:100],
        }


def body_checkin_reply(weight_kg: float | None, bmi: float | None, tags: list, notes: str) -> str:
    bmi_str = f"BMI {bmi}" if bmi else "no weight logged"
    tags_str = ", ".join(tags) if tags else "no tags"
    prompt = (
        f"Body check-in: {bmi_str}. Feel: {tags_str}. Notes/reflection: {notes or 'none'}.\n\n"
        "Give a Buff Buddy reply. Max 2 lines. Calm, read-the-room tone. "
        "If she feels strong → brief acknowledgement. If lethargic/stressed → steady, no toxic positivity. "
        "If she wrote a reflection on yesterday (e.g. skipped protein, overate sugar) → briefly acknowledge it and tie it to today."
    )
    return _call(prompt, max_tokens=120)


# ── Coaching ──────────────────────────────────────────────────────────────────

def generate_coaching_note(context: str, coach_nutrition: str, coach_training: str) -> str:
    prompt = (
        f"Nutrition guidelines:\n{coach_nutrition}\n\n"
        f"Training guidelines:\n{coach_training}\n\n"
        f"Today's data:\n{context}\n\n"
        "Give ONE coaching note (2-3 sentences). Direct. Reference actual numbers."
    )
    return _call(prompt, max_tokens=200)


def score_weekly_goals(goals_text: str, week_data: str) -> str:
    prompt = (
        f"Goals:\n{goals_text}\n\n"
        f"Week data:\n{week_data}\n\n"
        "Score each goal: Achieved / Partial / Missed. "
        "Overall score out of 10. 2-3 sentences of honest feedback. Buff Buddy voice."
    )
    return _call(prompt, max_tokens=500)


def generate_cycle_summary(cycle_data: dict) -> str:
    prompt = (
        f"Cycle data:\n{json.dumps(cycle_data, indent=2)}\n\n"
        "Write a monthly cycle summary in Buff Buddy's voice.\n\n"
        "Format:\n"
        "1. Casual 2-3 sentence intro (warm, not clinical). Use 'Liz'.\n"
        "2. Structured breakdown:\n"
        "   - Mood by phase (use the avg_mood_by_phase data)\n"
        "   - Training: gym sessions + activities\n"
        "   - Nutrition averages\n"
        "   - Any patterns worth noting\n\n"
        "Keep it real. No toxic positivity. Max 10 lines total."
    )
    return _call(prompt, max_tokens=600)


# ── Exercise catalogue AI ─────────────────────────────────────────────────────

def research_exercise(name: str) -> dict:
    """Look up muscle groups and basic info for an exercise."""
    prompt = (
        f"Exercise: {name}\n\n"
        "Reply as JSON only:\n"
        '{"muscle_group": "<primary muscle group>", "secondary": "<secondary muscles>", '
        '"category": "<push|pull|legs|core|arms|cardio>", "notes": "<one line tip>"}'
    )
    raw = _call(prompt, max_tokens=150)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return {"muscle_group": "Unknown", "secondary": "", "category": "", "notes": ""}


def suggest_extras_from_catalogue(
    catalogue: list,
    muscle_groups_done: list,
    count_needed: int,
    coach_notes: str = "",
) -> list:
    """Pick extras from optional exercises in catalogue."""
    optionals = [r for r in catalogue if str(r.get("Set", "")).lower() == "optional"]
    if not optionals:
        return []
    cat_str = "\n".join(
        f"- {r['Exercise Name']} ({r['Muscle Group']}) — last used: {r.get('Last Used', 'never')}"
        for r in optionals
    )
    done_str = ", ".join(muscle_groups_done) if muscle_groups_done else "none yet"
    prompt = (
        f"Available optional exercises:\n{cat_str}\n\n"
        f"Muscle groups already hit today: {done_str}\n"
        f"Coach notes: {coach_notes or 'none'}\n"
        f"Need {count_needed} extra exercise(s).\n\n"
        "Pick the best options prioritising: unused muscle groups, exercises not done recently, coach notes.\n"
        "Reply as JSON array only:\n"
        '[{"name": "Exercise Name", "reason": "one line why"}]'
    )
    raw = _call(prompt, max_tokens=300)
    start = raw.find("[")
    end = raw.rfind("]") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return []


def suggest_new_exercises_for_muscle(muscle_group: str, existing_names: list) -> list:
    """Suggest new exercises for a muscle group not already in catalogue."""
    existing_str = ", ".join(existing_names) if existing_names else "none"
    prompt = (
        f"Muscle group: {muscle_group}\n"
        f"Already in catalogue: {existing_str}\n\n"
        "Suggest 3 effective exercises NOT already in the list above.\n"
        "Reply as JSON array only:\n"
        '[{"name": "Exercise Name", "muscle_group": "<primary>", "notes": "<one line tip>"}]'
    )
    raw = _call(prompt, max_tokens=400)
    start = raw.find("[")
    end = raw.rfind("]") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return []


def parse_add_exercise_request(text: str) -> dict:
    """Extract exercise name (and optional set/notes) from natural language."""
    prompt = (
        f"Message: {text}\n\n"
        "Extract the exercise being added. Reply as JSON only:\n"
        '{"name": "Exercise Name", "set": "<set name if mentioned, else optional>", '
        '"sets": <integer or 3>, "notes": "<any extra detail or empty>"}'
    )
    raw = _call(prompt, max_tokens=150)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return {"name": text.strip(), "set": "optional", "sets": 3, "notes": ""}


def parse_create_set_request(text: str) -> dict:
    """Extract set name and exercises from 'create Push Day with X, Y, Z'."""
    prompt = (
        f"Message: {text}\n\n"
        "Extract the new set name and exercises. Reply as JSON only:\n"
        '{"set_name": "Set Name", "exercises": [{"name": "Exercise Name"}]}'
    )
    raw = _call(prompt, max_tokens=400)
    start = raw.find("{")
    end = raw.rfind("}") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return {"set_name": "New Set", "exercises": []}


def parse_target_muscle_request(text: str) -> str:
    """Extract the target muscle group from 'I want to hit X'."""
    prompt = (
        f"Message: {text}\n\n"
        "What muscle group does the user want to target? Reply with just the muscle group name, nothing else.\n"
        "Examples: chest, back, legs, shoulders, arms, core, glutes, hamstrings"
    )
    return _call(prompt, max_tokens=20).strip().lower()


def generate_motivation_message(trigger: str, context: str) -> str:
    prompt = f"Trigger: {trigger}\nContext: {context}\n\nShort Buff Buddy motivation. 1-2 sentences."
    return _call(prompt, max_tokens=100)


# ── Content Log ────────────────────────────────────────────────────────────────

_CONTENT_CONTEXT = """
Liz is on an 8-week fitness transformation. Content plan has 4 pillars:

PILLAR 1 — Transformation
  Angle A: Reflection Carousel — mindset shifts, what she's thinking differently about, still struggling with
  Angle B: Remember When — fears/limiting beliefs that feel different now

PILLAR 2 — Community
  Angle A: Fitness Dates — trying a class/activity with someone, what she learned
  Angle B: Short clips from fitness dates — beginner POV, trendy sounds

PILLAR 3 — Training
  Angle A: Training Plan / Splits — what she's doing each day, session breakdown
  Angle B: Internal Monologue — POV-style, what she thinks during training, not quitting

PILLAR 4 — Food & Recovery
  Angle A: Fuelling Performance — what she eats before/after, meals that keep her going
  Angle B: Recovery Rituals — sunday reset, sleep routine, walks, mobility, rest days
"""

def parse_content_idea(raw_note: str, week_num: int) -> dict:
    """Auto-classify a content thought into pillar, angle, and suggested content direction."""
    prompt = (
        f"{_CONTENT_CONTEXT}\n"
        f"Current week of transformation: Week {week_num}\n\n"
        f"Liz's raw note: \"{raw_note}\"\n\n"
        "Classify this and reply as JSON only:\n"
        '{"pillar": "<1|2|3|4> — <Pillar name>", "angle": "<A|B> — <Angle name>", '
        '"suggested_angle": "<one punchy sentence: how to turn this raw note into a post>"}\n\n'
        "Rules:\n"
        "- Pick the single best pillar + angle fit\n"
        "- suggested_angle: a concrete, specific content direction — not generic. Reference what she actually said.\n"
        "- If it genuinely fits multiple pillars, pick the most emotionally resonant one\n"
        "Reply with JSON only."
    )
    raw = _call(prompt, max_tokens=200)
    start, end = raw.find("{"), raw.rfind("}") + 1
    try:
        return json.loads(raw[start:end])
    except Exception:
        return {
            "pillar": "?",
            "angle": "?",
            "suggested_angle": "Needs manual review",
        }


def generate_weekly_reflection(week_label: str, stats: dict) -> str:
    """Short narrative reflection on the week — what happened, what to do better."""
    prompt = (
        f"Write a short end-of-week reflection for Liz. 3-4 sentences max. Honest, warm, direct — Buff Buddy voice.\n\n"
        f"Week: {week_label}\n"
        f"Weight: {stats.get('weight_summary', 'not logged')}\n"
        f"Nutrition: avg {stats.get('avg_cal', '?')}cal / {stats.get('avg_pro', '?')}gP (target {stats.get('cal_target')}cal / {stats.get('pro_target')}gP)\n"
        f"Days on calorie target: {stats.get('days_cal_ok', '?')}\n"
        f"Days on protein target: {stats.get('days_pro_ok', '?')}\n"
        f"Strength sessions: {stats.get('strength_sessions', 0)}/{stats.get('strength_target', 3)}\n"
        f"Cardio sessions: {stats.get('cardio_sessions', 0)}/{stats.get('cardio_target', 2)}\n"
        f"Sleep: avg {stats.get('avg_sleep', '?')}h, {stats.get('nights_7h', 0)} nights ≥7h\n\n"
        "What to reflect on:\n"
        "- What landed well this week\n"
        "- What slipped and why (be specific, not generic)\n"
        "- One clear thing to focus on next week\n\n"
        "Do NOT use bullet points or headers. Just flowing honest sentences. Keep it under 60 words."
    )
    return _call(prompt, max_tokens=150, system=BUFF_BUDDY_SYSTEM)
