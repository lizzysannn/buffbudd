"""Decides whether an incoming text message is a gym log, sleep reply, or food log."""
import re

# Explicit keyword triggers at the start of the message
_GYM_PREFIX = re.compile(r"^\s*(gym|GYM|training|TRAINING|workout|WORKOUT)\b", re.IGNORECASE)
_SLEEP_PREFIX = re.compile(r"^\s*(sleep|SLEEP|recovery|RECOVERY|slept|SLEPT)\b", re.IGNORECASE)

# Sleep reply: two numbers like "7 4" or "7.5 4"
_SLEEP_RE = re.compile(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$")


def classify_text(text: str) -> str:
    """Returns 'gym', 'sleep', 'food', or 'unknown'.

    Rules:
    - Starts with GYM / TRAINING / WORKOUT → gym
    - Starts with SLEEP / RECOVERY / SLEPT → sleep
    - Two numbers like '7 4' → sleep check-in reply
    - Everything else → food (Claude will estimate macros)
    """
    if _SLEEP_RE.match(text):
        return "sleep"
    if _GYM_PREFIX.match(text):
        return "gym"
    if _SLEEP_PREFIX.match(text):
        return "sleep"
    # Default: treat as food description
    return "food"
