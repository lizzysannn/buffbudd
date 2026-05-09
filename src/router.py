"""Decides whether an incoming text message is a gym log, sleep reply, or food log."""
import re

# Gym patterns: weight or sets/reps notation
_GYM_RE = re.compile(
    r"(\d+(\.\d+)?\s*(kg|lb|lbs))|(\d+\s*x\s*\d+)|(rpe\s*\d)|(sets?|reps?|bench|squat|deadlift|press|curl|row|pull)",
    re.IGNORECASE,
)
# Sleep reply: two numbers like "7 4" or "7.5 4"
_SLEEP_RE = re.compile(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$")

# Food keywords
_FOOD_RE = re.compile(
    r"\b(ate|eat|eating|had|have|meal|breakfast|lunch|dinner|snack|brunch|supper|"
    r"protein|calorie|carb|fat|gram|g\b|ml\b|cup|bowl|plate|serving|portion|"
    r"rice|chicken|beef|pork|fish|egg|bread|pasta|noodle|salad|soup|milk|"
    r"yogurt|oat|fruit|vegetable|coffee|juice|shake|smoothie|cook|cooked|raw|"
    r"fried|grilled|boiled|baked|steamed|soya|tofu|cheese|butter|oil|sauce|"
    r"whey|bar|banana|apple|avocado|sweet potato|potato|broccoli|spinach)\b",
    re.IGNORECASE,
)


def classify_text(text: str) -> str:
    """Returns 'gym', 'sleep', 'food', or 'unknown'."""
    if _SLEEP_RE.match(text):
        return "sleep"
    if _GYM_RE.search(text):
        return "gym"
    if _FOOD_RE.search(text):
        return "food"
    return "unknown"
