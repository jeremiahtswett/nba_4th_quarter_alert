import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from alerter import nba_client
from alerter.nba_client import FetchError, fetch_scoreboard

FIXTURES = Path(__file__).parent / "fixtures"

AKAMAI_DENIAL = (
    "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n"
    "<H1>Access Denied</H1>\nYou don't have permission to access ..."
)


def test_valid_json_returned():
    body = (FIXTURES / "close_game_q4.json").read_text(encoding="utf-8")
    with mock.patch.object(nba_client, "_request", return_value=(200, body)):
        data = fetch_scoreboard(retries=1)
    assert data["scoreboard"]["games"][0]["gameId"] == "0022500561"


def test_akamai_html_with_200_is_rejected():
    # A 200 status can still carry the denial page (spec section 9).
    with mock.patch.object(nba_client, "_request", return_value=(200, AKAMAI_DENIAL)):
        with mock.patch.object(nba_client.time, "sleep"):
            with pytest.raises(FetchError):
                fetch_scoreboard(retries=2)


def test_akamai_html_with_403_is_rejected():
    with mock.patch.object(nba_client, "_request", return_value=(403, AKAMAI_DENIAL)):
        with pytest.raises(FetchError):
            fetch_scoreboard(retries=1)


def test_json_without_games_key_is_rejected():
    with mock.patch.object(nba_client, "_request", return_value=(200, '{"unexpected": true}')):
        with pytest.raises(FetchError):
            fetch_scoreboard(retries=1)


def test_network_error_retries_then_succeeds():
    body = (FIXTURES / "no_games_today.json").read_text(encoding="utf-8")
    outcomes = [urllib.error.URLError("boom"), (200, body)]

    def side_effect():
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with mock.patch.object(nba_client, "_request", side_effect=side_effect):
        with mock.patch.object(nba_client.time, "sleep"):
            data = fetch_scoreboard(retries=2)
    assert data["scoreboard"]["games"] == []


def test_persistent_failure_raises_after_retries():
    with mock.patch.object(nba_client, "_request", side_effect=urllib.error.URLError("down")):
        with mock.patch.object(nba_client.time, "sleep") as sleep:
            with pytest.raises(FetchError):
                fetch_scoreboard(retries=3)
    assert sleep.call_count == 2  # backs off between attempts, not after the last


def test_browser_headers_and_https():
    assert "Mozilla" in nba_client.HEADERS["User-Agent"]
    assert nba_client.HEADERS["Referer"] == "https://www.nba.com/"
    assert nba_client.SCOREBOARD_URL.startswith("https://")
