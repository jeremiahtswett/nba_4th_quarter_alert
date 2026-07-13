import json
from pathlib import Path

import pytest

from alerter.trigger import REASON_CLOSE_Q4, REASON_OVERTIME, evaluate_game

FIXTURES = Path(__file__).parent / "fixtures"


def load_games(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)["scoreboard"]["games"]


def load_game(name):
    games = load_games(name)
    assert len(games) == 1
    return games[0]


# --- fixture-driven trigger decisions -------------------------------------

def test_close_q4_alerts():
    alert = evaluate_game(load_game("close_game_q4.json"))
    assert alert is not None
    assert alert.reason == REASON_CLOSE_Q4
    assert alert.game_id == "0022500561"
    assert alert.matchup == "LAL @ BOS"
    assert alert.away_score == 98
    assert alert.home_score == 101
    assert alert.clock_display == "3:42"
    assert alert.period_display == "Q4"


def test_blowout_q4_does_not_alert():
    assert evaluate_game(load_game("blowout_q4.json")) is None


def test_early_q2_close_score_does_not_alert():
    assert evaluate_game(load_game("early_game_q2.json")) is None


def test_halftime_empty_clock_does_not_alert_or_crash():
    assert evaluate_game(load_game("halftime.json")) is None


def test_overtime_alerts():
    alert = evaluate_game(load_game("overtime.json"))
    assert alert is not None
    assert alert.reason == REASON_OVERTIME
    assert alert.matchup == "MIL @ CLE"
    assert alert.period_display == "OT"


def test_final_game_does_not_alert():
    assert evaluate_game(load_game("final_game.json")) is None


def test_no_games_today_is_empty():
    assert load_games("no_games_today.json") == []


# --- boundary and edge cases ------------------------------------------------

def make_game(**overrides):
    game = json.loads(json.dumps(load_game("close_game_q4.json")))
    for key, value in overrides.items():
        if key in ("home_score", "away_score"):
            team = "homeTeam" if key == "home_score" else "awayTeam"
            game[team]["score"] = value
        else:
            game[key] = value
    return game


def test_exactly_five_minutes_does_not_alert():
    # spec: strictly less than 5:00 remaining
    assert evaluate_game(make_game(gameClock="PT05M00.00S")) is None


def test_just_under_five_minutes_alerts():
    assert evaluate_game(make_game(gameClock="PT04M59.90S")) is not None


def test_diff_of_exactly_five_alerts():
    assert evaluate_game(make_game(home_score=100, away_score=95)) is not None


def test_diff_of_six_does_not_alert():
    assert evaluate_game(make_game(home_score=100, away_score=94)) is None


def test_away_team_leading_counts_too():
    assert evaluate_game(make_game(home_score=95, away_score=99)) is not None


def test_q4_empty_clock_does_not_alert():
    # Between-play feed quirk: never treat an empty clock as < 5:00.
    assert evaluate_game(make_game(gameClock="")) is None


def test_scheduled_game_never_alerts():
    assert evaluate_game(make_game(gameStatus=1)) is None


def test_double_overtime_alerts_with_label():
    alert = evaluate_game(make_game(period=6, gameClock="PT05M00.00S", home_score=120, away_score=120))
    assert alert is not None
    assert alert.reason == REASON_OVERTIME
    assert alert.period_display == "2OT"


def test_overtime_with_empty_clock_still_alerts():
    # The clock is blank in the break before OT starts; alert immediately.
    alert = evaluate_game(make_game(period=5, gameClock=""))
    assert alert is not None
    assert alert.reason == REASON_OVERTIME
    assert alert.clock_display == ""


def test_overtime_blowout_still_alerts():
    # OT qualifies regardless of differential (spec section 2).
    assert evaluate_game(make_game(period=5, home_score=120, away_score=110)) is not None


def test_missing_score_treated_as_zero_not_close():
    game = make_game()
    del game["homeTeam"]["score"]
    assert evaluate_game(game) is None  # reads as 98-0: not close, and no crash


def test_missing_scores_do_not_crash():
    game = make_game()
    game["homeTeam"]["score"] = None
    game["awayTeam"]["score"] = None
    evaluate_game(game)  # must not raise
