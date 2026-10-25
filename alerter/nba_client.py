"""Fetching today's NBA scoreboard.

Originally pulled cdn.nba.com directly, but that feed sits behind Akamai bot
protection that blocks datacenter ASNs outright (GitHub Actions runner IPs
included) regardless of headers or TLS fingerprint — confirmed by a week of
scheduled runs all getting HTTP 403 "Access Denied" even with a full browser
header set. ESPN's public scoreboard API carries the same live data (score,
period, game clock) and isn't behind that wall, so we fetch from there and
normalize into the same internal shape the rest of this package expects
(NBA's own gameId/gameStatus/period/gameClock/teamTricode fields), so
trigger.py, runner.py, state.py and the test fixtures didn't need to change.
"""

import json
import logging
import time
import urllib.error
import urllib.request

from .config import SCOREBOARD_URL

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 15  # seconds

GAME_STATUS_SCHEDULED = 1
GAME_STATUS_LIVE = 2
GAME_STATUS_FINAL = 3

_STATE_TO_STATUS = {
    "in": GAME_STATUS_LIVE,
    "post": GAME_STATUS_FINAL,
}

# ESPN uses its own short codes for six teams instead of the standard NBA
# tricode; normalize so email subjects read the way fans expect.
_TRICODE_FIXUPS = {
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}


class FetchError(Exception):
    pass


def _request() -> tuple[int, str]:
    """Return (status, body). HTTP error statuses return their body too."""
    req = urllib.request.Request(SCOREBOARD_URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _game_clock(status: dict) -> str:
    """Render ESPN's clock (seconds remaining, float) as NBA's ISO-8601 duration.

    Empty string — same as the NBA feed between periods — whenever the clock
    isn't actively counting down for a live game.
    """
    if status.get("type", {}).get("state") != "in":
        return ""
    clock = status.get("clock")
    if clock is None:
        return ""
    minutes, seconds = divmod(float(clock), 60)
    return f"PT{int(minutes)}M{seconds:.2f}S"


def _team(competitor: dict) -> dict:
    abbreviation = competitor.get("team", {}).get("abbreviation", "???")
    return {
        "teamTricode": _TRICODE_FIXUPS.get(abbreviation, abbreviation),
        "score": competitor.get("score", "0"),
    }


def _normalize_game(event: dict) -> dict:
    competition = (event.get("competitions") or [{}])[0]
    status = event.get("status") or {}
    teams = {c.get("homeAway"): _team(c) for c in competition.get("competitors", [])}
    series = competition.get("series") or {}
    return {
        "gameId": str(event.get("id", "")),
        "gameStatus": _STATE_TO_STATUS.get(status.get("type", {}).get("state"), GAME_STATUS_SCHEDULED),
        "gameStatusText": status.get("type", {}).get("description", ""),
        "gameTimeUTC": event.get("date", ""),
        "period": status.get("period", 0),
        "gameClock": _game_clock(status),
        "homeTeam": teams.get("home", {"teamTricode": "???", "score": "0"}),
        "awayTeam": teams.get("away", {"teamTricode": "???", "score": "0"}),
        "seriesText": series.get("summary", ""),
    }


def _parse_scoreboard(status: int, body: str) -> dict:
    """Validate the body is real scoreboard JSON; raise FetchError otherwise."""
    if status != 200:
        raise FetchError(f"HTTP {status}: {body[:120]!r}")
    try:
        data = json.loads(body)
    except ValueError:
        raise FetchError(f"Response body is not JSON: {body[:120]!r}") from None
    events = data.get("events")
    if not isinstance(events, list):
        raise FetchError("JSON response missing events[]")
    return {
        "scoreboard": {
            "gameDate": data.get("day", {}).get("date", ""),
            "games": [_normalize_game(e) for e in events],
        }
    }


def fetch_scoreboard(retries: int = 3, backoff_seconds: float = 3.0) -> dict:
    """Fetch today's scoreboard, retrying transient failures.

    Returns the parsed payload (with scoreboard.games[] guaranteed present)
    or raises FetchError after exhausting retries.
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            status, body = _request()
            return _parse_scoreboard(status, body)
        except (urllib.error.URLError, OSError, FetchError) as exc:
            last_error = exc
            log.warning("Scoreboard fetch attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    raise FetchError(f"Scoreboard fetch failed after {retries} attempts: {last_error}")
