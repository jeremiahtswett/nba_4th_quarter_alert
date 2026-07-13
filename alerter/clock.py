"""Parsing for the feed's ISO-8601 duration game clock (e.g. "PT04M32.00S").

The clock is an empty string whenever a game isn't actively playing
(scheduled, halftime, between quarters) — that must parse to None, never
crash or read as 0:00 remaining.
"""

import re
from typing import Optional

_CLOCK_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")


def parse_game_clock(value) -> Optional[float]:
    """Return seconds remaining, or None if the clock is empty or unparseable."""
    if not value or not isinstance(value, str):
        return None
    match = _CLOCK_RE.match(value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def format_clock(seconds: float) -> str:
    """Render seconds as M:SS for email display (e.g. 222.0 -> "3:42")."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"
