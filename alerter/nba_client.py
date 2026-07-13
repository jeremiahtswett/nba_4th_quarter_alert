"""Fetching the NBA live scoreboard feed.

The feed sits behind Akamai and returns an HTML "Access Denied" page — with
a 200 or 403 status — when requested over plain HTTP or without a browser-like
User-Agent. The content-type is text/plain even for valid JSON, so the only
trustworthy validation is parsing the body and checking its shape.

Uses stdlib urllib rather than requests deliberately: Akamai also blocks on
TLS fingerprint, and urllib3's customized TLS handshake gets denied where the
stdlib ssl default is accepted (verified empirically — same headers, requests
gets 403, urllib gets 200). Bonus: zero third-party runtime dependencies.
"""

import json
import logging
import time
import urllib.error
import urllib.request

from .config import SCOREBOARD_URL

log = logging.getLogger(__name__)

# Browser-like headers; a bare default User-Agent gets blocked.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
}

REQUEST_TIMEOUT = 15  # seconds


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


def _log_if_denial(body: str) -> None:
    if "Access Denied" in body[:500]:
        # Loud, specific log: this usually means the User-Agent/HTTPS
        # requirement regressed (spec section 9).
        log.error("Feed returned an Akamai 'Access Denied' page — check HTTPS/User-Agent headers")


def _parse_scoreboard(status: int, body: str) -> dict:
    """Validate the body is real scoreboard JSON; raise FetchError otherwise."""
    if status != 200:
        _log_if_denial(body)
        raise FetchError(f"HTTP {status}: {body[:120]!r}")
    try:
        data = json.loads(body)
    except ValueError:
        # A 200 can still carry the Akamai denial page instead of JSON.
        _log_if_denial(body)
        raise FetchError(f"Response body is not JSON: {body[:120]!r}") from None
    games = data.get("scoreboard", {}).get("games")
    if not isinstance(games, list):
        raise FetchError("JSON response missing scoreboard.games[]")
    return data


def fetch_scoreboard(retries: int = 3, backoff_seconds: float = 3.0) -> dict:
    """Fetch today's scoreboard, retrying transient failures.

    Returns the parsed payload (with scoreboard.games[] guaranteed present)
    or raises FetchError after exhausting retries. Never returns HTML-denial
    or shape-mismatched data.
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
