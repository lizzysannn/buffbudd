"""Claude-powered intent classification for incoming messages."""
import re
import anthropic
from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Period trigger patterns — detected before Claude to avoid API call
_PERIOD_RE = re.compile(
    r"\b(period\s+started|got\s+my\s+period|period\s+today|started\s+my\s+period|"
    r"period\s+came|my\s+period|got\s+period|period\s+start)\b",
    re.IGNORECASE,
)

# Sleep shorthand: two numbers like "7 4" or "7.5 4"
_SLEEP_RE = re.compile(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$")

# Food query: asking to review past food logs
_FOOD_QUERY_RE = re.compile(
    r"\b(what\s+did\s+i\s+eat|what\s+i\s+ate|show\s+me\s+(my\s+)?(food|meals?)|"
    r"summary\s+for\s+(yesterday|last|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(food|meal|eating)\s+summary|what\s+was\s+my\s+(lunch|breakfast|dinner|meal)|"
    r"tell\s+me\s+(my\s+)?(food|meal|eating|what\s+i\s+ate|summary))\b",
    re.IGNORECASE,
)


def classify_intent(text: str) -> str:
    """Returns: gym | meal | recovery | emotions | period |
                add_exercise | create_set | target_muscle | unknown

    Uses cheap regex first, then Claude for ambiguous cases.
    """
    # Fast-path: sleep shorthand
    if _SLEEP_RE.match(text.strip()):
        return "recovery"

    # Fast-path: period trigger
    if _PERIOD_RE.search(text):
        return "period"

    # Fast-path: food query
    if _FOOD_QUERY_RE.search(text):
        return "food_query"

    # Claude classification
    prompt = (
        "Classify this Telegram message from a fitness tracking user into exactly one intent.\n\n"
        "Intents:\n"
        "- gym: logging a workout, exercises, sets, reps, weights, or saying they went to the gym\n"
        "- meal: describing food eaten, asking to log a meal, food photos\n"
        "- recovery: sleep hours, sleep quality, rest, fatigue, recovery\n"
        "- emotions: mood, feelings, stress, mental state, energy levels, how they feel\n"
        "- period: period started, menstrual cycle related\n"
        "- add_exercise: adding a new exercise to the catalogue (e.g. 'add Romanian Deadlift')\n"
        "- create_set: creating a new workout set (e.g. 'create Push Day with Bench, OHP')\n"
        "- target_muscle: user wants to hit a specific muscle group (e.g. 'I want to hit chest', 'what should I do for legs')\n"
        "- food_query: asking to see/review food logged on a past or specific day (e.g. 'what did I eat yesterday', 'show me yesterday's food', 'what was my lunch last Monday')\n"
        "- unknown: anything else\n\n"
        f"Message: {text}\n\n"
        "Reply with exactly one word from: gym, meal, recovery, emotions, period, add_exercise, create_set, target_muscle, food_query, unknown"
    )
    response = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    intent = response.content[0].text.strip().lower()
    valid = {"gym", "meal", "recovery", "emotions", "period", "add_exercise", "create_set", "target_muscle", "food_query", "unknown"}
    return intent if intent in valid else "unknown"
