import json
import urllib.error
from unittest import mock

import pytest

from alerter import nba_client
from alerter.nba_client import FetchError, fetch_scoreboard


def _espn_event(
    event_id="401909861",
    state="in",
    period=4,
    clock=142.0,
    home_score="101",
    away_score="98",
):
    return {
        "id": event_id,
        "date": "2026-10-25T21:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "UTA"}, "score": home_score},
                    {"homeAway": "away", "team": {"abbreviation": "LAL"}, "score": away_score},
                ],
            }
        ],
        "status": {
            "clock": clock,
            "period": period,
            "type": {"state": state, "description": "In Progress"},
        },
    }


def _espn_payload(events):
    return json.dumps({"day": {"date": "2026-10-25"}, "events": events})


def test_valid_json_is_normalized_to_internal_shape():
    body = _espn_payload([_espn_event()])
    with mock.patch.object(nba_client, "_request", return_value=(200, body)):
        data = fetch_scoreboard(retries=1)
    game = data["scoreboard"]["games"][0]
    assert game["gameId"] == "401909861"
    assert game["gameStatus"] == nba_client.GAME_STATUS_LIVE
    assert game["period"] == 4
    assert game["gameClock"] == "PT2M22.00S"
    assert game["homeTeam"] == {"teamTricode": "UTA", "score": "101"}
    assert game["awayTeam"] == {"teamTricode": "LAL", "score": "98"}


def test_scheduled_and_final_games_get_empty_clock():
    events = [_espn_event(event_id="1", state="pre"), _espn_event(event_id="2", state="post")]
    body = _espn_payload(events)
    with mock.patch.object(nba_client, "_request", return_value=(200, body)):
        data = fetch_scoreboard(retries=1)
    games = {g["gameId"]: g for g in data["scoreboard"]["games"]}
    assert games["1"]["gameStatus"] == nba_client.GAME_STATUS_SCHEDULED
    assert games["1"]["gameClock"] == ""
    assert games["2"]["gameStatus"] == nba_client.GAME_STATUS_FINAL
    assert games["2"]["gameClock"] == ""


def test_no_games_today():
    body = _espn_payload([])
    with mock.patch.object(nba_client, "_request", return_value=(200, body)):
        data = fetch_scoreboard(retries=1)
    assert data["scoreboard"]["games"] == []


def test_non_200_status_is_rejected():
    with mock.patch.object(nba_client, "_request", return_value=(403, "Forbidden")):
        with pytest.raises(FetchError):
            fetch_scoreboard(retries=1)


def test_non_json_body_is_rejected():
    with mock.patch.object(nba_client, "_request", return_value=(200, "not json")):
        with pytest.raises(FetchError):
            fetch_scoreboard(retries=1)


def test_json_without_events_key_is_rejected():
    with mock.patch.object(nba_client, "_request", return_value=(200, '{"unexpected": true}')):
        with pytest.raises(FetchError):
            fetch_scoreboard(retries=1)


def test_network_error_retries_then_succeeds():
    body = _espn_payload([])
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


def test_nonstandard_tricodes_are_fixed_up():
    event = _espn_event()
    event["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "UTAH"
    event["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "NY"
    body = _espn_payload([event])
    with mock.patch.object(nba_client, "_request", return_value=(200, body)):
        data = fetch_scoreboard(retries=1)
    game = data["scoreboard"]["games"][0]
    assert game["homeTeam"]["teamTricode"] == "UTA"
    assert game["awayTeam"]["teamTricode"] == "NYK"


def test_browser_user_agent_and_https():
    assert "Mozilla" in nba_client.HEADERS["User-Agent"]
    assert nba_client.SCOREBOARD_URL.startswith("https://")
