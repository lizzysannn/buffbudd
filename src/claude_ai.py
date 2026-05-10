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

def analyse_food_photo(image_bytes: bytes, mime_type: str = "image/jpeg", caption_hint: str = "") -> dict:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    hint_line = f"\nUser description: {caption_hint}" if caption_hint else ""
    prompt = (
        "Analyse this meal photo and estimate macros per item. "
        "The user's description takes priority over what you see — use it to correct your visual guess. "
        "Include EVERY item mentioned, even if macros are near zero (e.g. black coffee = ~5 cal)."
        f"{hint_line}\n\n"
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


def analyse_food_text(text: str) -> dict:
    prompt = (
        "Analyse this meal description and estimate macros per item. "
        "Include EVERY item mentioned, even if macros are near zero (e.g. black coffee = ~5 cal, water = 0).\n\n"
        "Reply in this exact JSON format, nothing else:\n"
        "{\n"
        '  "meal_type": "breakfast|lunch|dinner|snack|supper|NONE",\n'
        '  "note": "one-line Buff Buddy style response",\n'
        '  "items": [\n'
        '    {"name": "item name", "calories": 0, "protein": 0.0, "carbs": 0.0, "fats": 0.0}\n'
        "  ]\n"
        "}\n\n"
        f"Meal: {text}"
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
    description = ", ".join(i["name"] for i in items if i.get("name"))

    return {
        "description": description,
        "meal_type": meal_type,
        "calories": total_cal,
        "protein": round(total_pro, 1),
        "carbs": round(total_carb, 1),
        "fats": round(total_fat, 1),
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
        '[{"number":1,"exercise":"name","weight_kg":0,"sets":0,"reps":0,"rpe":null,"skipped":false,"notes":""}]\n\n'
        "Rules: match by number or name. If not mentioned set skipped:true. weight_kg=0 for bodyweight."
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


# ── Date extraction ───────────────────────────────────────────────────────────

def extract_log_date(text: str) -> str | None:
    """Return ISO date string if message refers to a past day, else None (= today)."""
    from datetime import date
    today = date.today()
    prompt = (
        f"Today is {today.strftime('%Y-%m-%d')} ({today.strftime('%A')}).\n"
        "Does this message refer to a specific past date (yesterday, last night, Monday, etc.)?\n"
        "If yes, reply with just the date in YYYY-MM-DD format.\n"
        "If it refers to today or no specific date, reply with: today\n\n"
        f"Message: {text}\n\n"
        "Reply with YYYY-MM-DD or 'today' only."
    )
    raw = _call(prompt, max_tokens=15).strip().lower()
    if raw == "today" or not raw:
        return None
    try:
        from datetime import date as _date
        _date.fromisoformat(raw)
        return raw if raw < today.isoformat() else None
    except ValueError:
        return None


# ── Recovery / Sleep ──────────────────────────────────────────────────────────

def parse_sleep(text: str) -> dict:
    """Parse natural sleep description into hours and qualitative notes."""
    prompt = (
        "Parse this sleep description and return JSON only:\n"
        '{"hours": <total sleep hours as float>, "notes": "<capture everything mentioned: dreams, meditation, interruptions, restlessness, anything>"}\n\n'
        "Rules:\n"
        "- Calculate TOTAL sleep by adding up all sleep periods\n"
        "- If times given, calculate duration mathematically\n"
        "- notes: copy across every detail they mentioned — don't summarise, just note it all\n\n"
        f"Message: {text}\n\n"
        "Reply with JSON only."
    )
    raw = _call(prompt, max_tokens=100)
    start, end = raw.find("{"), raw.rfind("}") + 1
    try:
        data = json.loads(raw[start:end])
        return {
            "hours": float(data.get("hours", 6)),
            "notes": data.get("notes", ""),
        }
    except Exception:
        return {"hours": 6.0, "notes": text}


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
