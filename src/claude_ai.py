"""All Claude API calls — food vision, gym parsing, coaching."""
import base64
import re
import anthropic
from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def analyse_food_photo(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Return estimated macros from a meal photo."""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are a precise sports nutritionist AI. "
        "Analyse this meal photo and estimate macros. "
        "Reply in this exact format, nothing else:\n"
        "DESCRIPTION: <brief meal description>\n"
        "CALORIES: <integer>\n"
        "PROTEIN: <grams as decimal>\n"
        "CARBS: <grams as decimal>\n"
        "FATS: <grams as decimal>\n"
        "CONFIDENCE: <low|medium|high>\n"
        "NOTE: <one-line note about your estimate>"
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = response.content[0].text
    return _parse_macro_response(text)


def _parse_macro_response(text: str) -> dict:
    result = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower()] = val.strip()
    return {
        "description": result.get("description", "Unknown meal"),
        "calories": int(result.get("calories", 0)),
        "protein": float(result.get("protein", 0)),
        "carbs": float(result.get("carbs", 0)),
        "fats": float(result.get("fats", 0)),
        "confidence": result.get("confidence", "medium"),
        "note": result.get("note", ""),
    }


def parse_gym_entry(text: str) -> dict:
    """Parse free-text gym log like 'Bench 80kg 4x5 RPE 8'."""
    prompt = (
        "Parse this gym log entry into structured data. "
        "Reply in this exact format, nothing else:\n"
        "EXERCISE: <exercise name, title case>\n"
        "SETS: <integer>\n"
        "REPS: <integer>\n"
        "WEIGHT: <kg as decimal, 0 if bodyweight>\n"
        "RPE: <decimal 1-10, or NONE>\n"
        "NOTES: <any extra detail, or empty>\n\n"
        f"Entry: {text}"
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text
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


def generate_coaching_note(
    context: str,
    coach_nutrition: str,
    coach_training: str,
    tone: str = "firm but supportive, like a personal trainer",
) -> str:
    prompt = (
        f"You are a personal coach. Tone: {tone}.\n\n"
        f"Nutrition coaching guidelines:\n{coach_nutrition}\n\n"
        f"Training coaching guidelines:\n{coach_training}\n\n"
        f"Today's data:\n{context}\n\n"
        "Give ONE short coaching note (2-3 sentences max) relevant to today's data. "
        "Be direct. No fluff."
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def score_weekly_goals(goals_text: str, week_data: str) -> str:
    prompt = (
        "You are a personal coach scoring weekly goals.\n\n"
        f"Goals set this week:\n{goals_text}\n\n"
        f"Week's data summary:\n{week_data}\n\n"
        "Score each goal as Achieved / Partial / Missed. "
        "Then give an overall score out of 10 and 2-3 sentences of honest feedback. "
        "Be firm but fair."
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def generate_motivation_message(trigger: str, context: str) -> str:
    prompt = (
        f"Trigger: {trigger}\nContext: {context}\n\n"
        "Write a short motivational message (1-2 sentences) in a firm, PT-style tone. "
        "No emojis. Be real."
    )
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()
