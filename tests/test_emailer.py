import json
from pathlib import Path

from alerter.emailer import build_body, build_subject
from alerter.trigger import evaluate_game

FIXTURES = Path(__file__).parent / "fixtures"


def alert_from(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        game = json.load(f)["scoreboard"]["games"][0]
    alert = evaluate_game(game)
    assert alert is not None
    return alert


def test_close_game_subject_matches_spec_format():
    subject = build_subject(alert_from("close_game_q4.json"))
    assert subject == "\U0001f6a8 Close game: LAL 98 – BOS 101, 3:42 left in Q4"


def test_overtime_subject():
    subject = build_subject(alert_from("overtime.json"))
    assert subject == "\U0001f6a8 Overtime: MIL 114 – CLE 114 (OT)"


def test_body_contains_matchup_score_clock_period():
    body = build_body(alert_from("close_game_q4.json"))
    assert "LAL @ BOS" in body
    assert "LAL 98 – BOS 101" in body
    assert "3:42" in body
    assert "Q4" in body
