import datetime
import json
from pathlib import Path
from unittest import mock

from alerter import runner
from alerter.config import PACIFIC, EmailConfig
from alerter.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def pt(hour, minute):
    return datetime.datetime(2026, 1, 15, hour, minute, tzinfo=PACIFIC)


def test_dead_zone_covers_late_night_only():
    assert not runner.in_dead_zone(pt(0, 45))   # multi-OT spillover: keep polling
    assert runner.in_dead_zone(pt(1, 0))
    assert runner.in_dead_zone(pt(4, 30))
    assert not runner.in_dead_zone(pt(8, 0))
    assert not runner.in_dead_zone(pt(19, 0))


def test_soft_cutoff():
    assert not runner.past_soft_cutoff(pt(23, 29))
    assert runner.past_soft_cutoff(pt(23, 30))


def test_next_tip_off_picks_earliest():
    games = [
        {"gameTimeUTC": "2026-01-16T02:00:00Z"},
        {"gameTimeUTC": "2026-01-16T00:30:00Z"},
        {"gameTimeUTC": "not-a-time"},
    ]
    tip = runner.next_tip_off(games)
    assert tip == datetime.datetime(2026, 1, 16, 0, 30, tzinfo=datetime.timezone.utc)
    assert runner.next_tip_off([{"gameTimeUTC": "garbage"}]) is None


def load_games(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)["scoreboard"]["games"]


def make_cfg():
    return EmailConfig(sender="s@example.com", app_password="pw", recipient="r@example.com")


def test_process_alerts_sends_once_and_dedupes(tmp_path):
    games = load_games("close_game_q4.json")
    state = StateStore(str(tmp_path / "state.json"))
    with mock.patch.object(runner.emailer, "send_alert", return_value=True) as send:
        runner.process_alerts(games, state, make_cfg())   # restart-past-trigger: fires immediately
        runner.process_alerts(games, state, make_cfg())   # second poll: deduped
    assert send.call_count == 1
    assert state.is_alerted("0022500561")


def test_process_alerts_retries_next_poll_after_email_failure(tmp_path):
    games = load_games("close_game_q4.json")
    state = StateStore(str(tmp_path / "state.json"))
    with mock.patch.object(runner.emailer, "send_alert", side_effect=[False, True]) as send:
        runner.process_alerts(games, state, make_cfg())   # send fails -> not marked
        assert not state.is_alerted("0022500561")
        runner.process_alerts(games, state, make_cfg())   # retried and marked
    assert send.call_count == 2
    assert state.is_alerted("0022500561")


def test_process_alerts_ignores_non_qualifying_games(tmp_path):
    games = load_games("blowout_q4.json") + load_games("final_game.json")
    state = StateStore(str(tmp_path / "state.json"))
    with mock.patch.object(runner.emailer, "send_alert", return_value=True) as send:
        runner.process_alerts(games, state, make_cfg())
    assert send.call_count == 0
