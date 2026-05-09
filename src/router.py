"""Decides whether an incoming text message is a gym log or sleep reply."""
import re

# Gym patterns: weight or sets/reps notation
_GYM_RE = re.compile(
    r"(\d+(\.\d+)?\s*(kg|lb|lbs))|(\d+\s*x\s*\d+)|(rpe\s*\d)",
    re.IGNORECASE,
)
# Sleep reply: two numbers like "7 4" or "7.5 4"
_SLEEP_RE = re.compile(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$")


def classify_text(text: str) -> str:
    """Returns 'gym', 'sleep', or 'unknown'."""
    if _SLEEP_RE.match(text):
        return "sleep"
    if _GYM_RE.search(text):
        return "gym"
    return "unknown"
