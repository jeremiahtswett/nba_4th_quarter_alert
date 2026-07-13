import datetime
import json

from alerter.state import StateStore


def make_store(tmp_path, contents=None):
    path = tmp_path / "alerted_games.json"
    if contents is not None:
        path.write_text(json.dumps(contents), encoding="utf-8")
    return StateStore(str(path))


def test_fresh_store_has_nothing_alerted(tmp_path):
    store = make_store(tmp_path)
    assert not store.is_alerted("0022500561")


def test_mark_alerted_persists_across_restarts(tmp_path):
    store = make_store(tmp_path)
    store.mark_alerted("0022500561")
    assert store.is_alerted("0022500561")

    reloaded = StateStore(store.path)  # simulate a process restart
    assert reloaded.is_alerted("0022500561")
    assert not reloaded.is_alerted("0022500562")


def test_one_email_per_game_ever(tmp_path):
    store = make_store(tmp_path)
    store.mark_alerted("0022500561")
    store.mark_alerted("0022500561")
    reloaded = StateStore(store.path)
    assert list(json.load(open(store.path))["alerted"]) == ["0022500561"]
    assert reloaded.is_alerted("0022500561")


def test_corrupt_state_file_starts_fresh_without_crashing(tmp_path):
    path = tmp_path / "alerted_games.json"
    path.write_text("{not json", encoding="utf-8")
    store = StateStore(str(path))
    assert not store.is_alerted("anything")
    store.mark_alerted("0022500561")  # and it can save over the corrupt file
    assert StateStore(str(path)).is_alerted("0022500561")


def test_old_entries_are_pruned(tmp_path):
    old_date = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    recent_date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    store = make_store(
        tmp_path,
        {"alerted": {"old-game": old_date, "recent-game": recent_date}, "last_heartbeat": None},
    )
    store.mark_alerted("new-game")
    reloaded = StateStore(store.path)
    assert not reloaded.is_alerted("old-game")
    assert reloaded.is_alerted("recent-game")
    assert reloaded.is_alerted("new-game")


def test_heartbeat_due_and_recorded(tmp_path):
    store = make_store(tmp_path)
    assert store.heartbeat_due(7)
    store.record_heartbeat()
    assert not store.heartbeat_due(7)
    assert StateStore(store.path).heartbeat_due(0)  # due immediately at 0-day interval


def test_no_git_commit_outside_actions(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_COMMIT_STATE", raising=False)
    store = make_store(tmp_path)
    assert not store.commit_enabled
