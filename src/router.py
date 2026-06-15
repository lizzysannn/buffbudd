"""Claude-powered intent classification for incoming messages."""
import re
import anthropic
from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Content / reflection / do better — must fire before gym regex
_CONTENT_TRIGGER_RE = re.compile(
    r"^\s*(content|reflection|do\s+better)\b",
    re.IGNORECASE,
)

# Period trigger patterns — detected before Claude to avoid API call
_PERIOD_RE = re.compile(
    r"\b(period\s+started|got\s+my\s+period|period\s+today|started\s+my\s+period|"
    r"period\s+came|my\s+period|got\s+period|period\s+start)\b",
    re.IGNORECASE,
)

# Sleep shorthand: two numbers like "7 4" or "7.5 4"
_SLEEP_RE = re.compile(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$")

# Gym fast-path: "gym", "self train", set names etc — before Claude
_GYM_RE = re.compile(
    r"\b(gym|gyming|workout|self[\s-]train(ing)?|hit\s+the\s+gym|going\s+to\s+(the\s+)?gym|"
    r"train(ing)?\s+today|today('?s)?\s+(gym|workout|training)|"
    r"wanna\s+gym|gonna\s+gym|going\s+gym|"
    r"ran|run(ning)?|morning\s+run|\d+\s*km\s+run|run\s+\d+\s*km|"
    r"stairmaster|treadmill|incline\s+walk)\b",
    re.IGNORECASE,
)

# Food query: asking specifically about food/meals logged
_FOOD_QUERY_RE = re.compile(
    r"\b(what\s+did\s+i\s+eat|what\s+i\s+ate|show\s+me\s+(my\s+)?(food|meals?)|"
    r"(food|meal|eating)\s+summary|what\s+was\s+my\s+(lunch|breakfast|dinner|meal))\b",
    re.IGNORECASE,
)

# Stats / summary query: asking for overall daily or weekly stats
_STATS_RE = re.compile(
    r"\b(stats|my\s+stats|give\s+me\s+(my\s+)?(stats|summary|overview|recap)|"
    r"tell\s+me\s+(my\s+)?(stats|summary|overview|progress|results?)|"
    r"show\s+(me\s+)?(my\s+)?(stats|summary|overview|progress|results?|log)|"
    r"how\s+(did\s+i\s+do|am\s+i\s+doing)|"
    r"(weekly|daily|today'?s?|yesterday'?s?|yest'?s?)\s+(summary|stats|recap|overview|results?|log)|"
    r"(log|summary|stats)\s+(from|for)\s+(today|yesterday|yest|this\s+week|last\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"summary\s+for\s+(today|yesterday|yest|this\s+week|last\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)

# Plain rejection — clears any pending state and shows menu
_NO_RE = re.compile(
    r"^(no|nope|nah|never\s*mind|nevermind|cancel|nvm|not\s+now|stop|skip\s+it)\.?$",
    re.IGNORECASE,
)

# Done for the day: end-of-day wrap-up
_DONE_RE = re.compile(
    r"\b(done\s+for\s+(the\s+)?day|i'?m\s+done\s+for\s+(the\s+)?day|that'?s?\s+(it\s+)?for\s+today|"
    r"done\s+for\s+today|calling\s+it\s+(a\s+day)?|wrapping\s+up(\s+today)?|"
    r"end\s+of\s+(my\s+)?day|end\s+(the\s+)?day|ready\s+to\s+end(\s+the\s+day)?|"
    r"day'?s?\s+done|finished\s+for\s+(the\s+)?day|that'?s?\s+a\s+wrap)\b",
    re.IGNORECASE,
)

# Body check-in: weight + body feel tags
_BODY_RE = re.compile(
    r"(\b\d+\.?\d*\s*kg\b"                              # weight in kg
    r"|\b(lethargic|bloated|sore|energised|brain\s+fog)\b"  # body-specific tags
    r"|\b(morning\s+weight|weighed\s+(in|myself)|body\s+(check|scan|feel))\b"
    r"|\bfeeling\s+(strong|lethargic|sore|bloated|groggy)\b)",
    re.IGNORECASE,
)


def classify_intent(text: str) -> str:
    """Returns: gym | meal | recovery | emotions | period | body_check |
                add_exercise | create_set | target_muscle | food_query |
                stats_query | done_for_day | unknown

    Uses cheap regex first, then Claude for ambiguous cases.
    """
    # Fast-path: content/reflection/do better (must be before gym regex)
    if _CONTENT_TRIGGER_RE.match(text):
        return "content"

    # Fast-path: done for the day
    if _DONE_RE.search(text):
        return "done_for_day"

    # Fast-path: sleep shorthand
    if _SLEEP_RE.match(text.strip()):
        return "recovery"

    # Fast-path: gym trigger
    if _GYM_RE.search(text):
        return "gym"

    # Fast-path: period trigger
    if _PERIOD_RE.search(text):
        return "period"

    # Fast-path: food query (meal-specific)
    if _FOOD_QUERY_RE.search(text):
        return "food_query"

    # Fast-path: general stats / summary query
    if _STATS_RE.search(text):
        return "stats_query"

    # Fast-path: body check-in (weight / body-feel tags)
    if _BODY_RE.search(text):
        return "body_check"

    # Claude classification
    prompt = (
        "Classify this Telegram message from a fitness tracking user into exactly one intent.\n\n"
        "Intents:\n"
        "- gym: logging a workout, exercises, sets, reps, weights, or saying they went to the gym\n"
        "- meal: describing food eaten, asking to log a meal, food photos\n"
        "- recovery: sleep hours, sleep quality, rest, fatigue, recovery\n"
        "- emotions: mood, feelings, stress, mental state, energy levels, how they feel emotionally\n"
        "- period: period started, menstrual cycle related\n"
        "- body_check: morning weight (kg), body feel (lethargic/strong/sore/bloated/stressed), daily body check-in\n"
        "- add_exercise: adding a new exercise to the catalogue (e.g. 'add Romanian Deadlift')\n"
        "- create_set: creating a new workout set (e.g. 'create Push Day with Bench, OHP')\n"
        "- target_muscle: user wants to hit a specific muscle group (e.g. 'I want to hit chest', 'what should I do for legs')\n"
        "- food_query: asking to see specifically what food/meals were logged (e.g. 'what did I eat yesterday', 'show me my meals')\n"
        "- stats_query: asking for overall stats, summary, or results for a day or week (e.g. 'tell me my stats for yesterday', 'give me my summary', 'how did I do this week')\n"
        "- content: sharing a content idea, reflection, thought about the journey, something she noticed, felt, or experienced that could become a post (e.g. 'i was thinking today about how far i've come', 'content idea:', 'this could be a good post', 'remember when i used to...')\n"
        "- unknown: anything else\n\n"
        f"Message: {text}\n\n"
        "Reply with exactly one word from: gym, meal, recovery, emotions, period, body_check, add_exercise, create_set, target_muscle, food_query, stats_query, content, unknown"
    )
    response = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    intent = response.content[0].text.strip().lower()
    valid = {"gym", "meal", "recovery", "emotions", "period", "body_check",
             "add_exercise", "create_set", "target_muscle", "food_query", "stats_query",
             "done_for_day", "content", "do_better", "unknown"}
    return intent if intent in valid else "unknown"
