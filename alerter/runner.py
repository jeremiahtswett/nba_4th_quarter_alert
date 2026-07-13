"""Schedule-aware polling loop (spec section 5).

Each invocation (a GitHub Actions run, or a local `python -m alerter run`):
  - exits in seconds when there are no games today (the entire off-season cost),
  - exits when the next tip-off is far away (a later 30-minute cron covers it),
  - otherwise polls every 30s, alerting and deduping, until all games are
    final, the Pacific-time window closes, or the runtime cap is reached
    (the next queued cron resumes seamlessly).

All window logic is computed in America/Los_Angeles; the feed's times are UTC.
"""

import datetime
import json
import logging
import time

from . import config, emailer, nba_client
from .state import StateStore
from .trigger import GAME_STATUS_LIVE, GAME_STATUS_SCHEDULED, evaluate_game

log = logging.getLogger(__name__)

# Give up the run (not the night — the next cron retries) after this many
# consecutive fetch failures, so a feed outage can't pin a runner for hours.
MAX_CONSECUTIVE_FAILURES = 10
FAILURE_SLEEP_SECONDS = 60


def now_pt() -> datetime.datetime:
    return datetime.datetime.now(config.PACIFIC)


def in_dead_zone(now: datetime.datetime) -> bool:
    """1:00 AM–8:00 AM PT: hard cutoff, no NBA game is ever live then."""
    hard_h, hard_m = config.HARD_CUTOFF_PT
    after_hard = (now.hour, now.minute) >= (hard_h, hard_m)
    return after_hard and now.hour < 8


def past_soft_cutoff(now: datetime.datetime) -> bool:
    return (now.hour, now.minute) >= config.SOFT_CUTOFF_PT


def parse_tip_time(game: dict):
    try:
        return datetime.datetime.fromisoformat(str(game.get("gameTimeUTC", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def next_tip_off(pending: list) -> datetime.datetime | None:
    tips = [t for t in (parse_tip_time(g) for g in pending) if t is not None]
    return min(tips) if tips else None


def maybe_heartbeat(state: StateStore) -> None:
    if state.heartbeat_due(config.HEARTBEAT_INTERVAL_DAYS):
        state.record_heartbeat()
        log.info("Heartbeat recorded — scheduler is alive")


def process_alerts(games: list, state: StateStore, cfg: config.EmailConfig) -> None:
    for game in games:
        game_id = str(game.get("gameId", ""))
        if not game_id or state.is_alerted(game_id):
            continue
        alert = evaluate_game(game)
        if alert is None:
            continue
        log.info(
            "TRIGGER (%s): %s %d – %s %d, %s %s",
            alert.reason, alert.away_tricode, alert.away_score,
            alert.home_tricode, alert.home_score,
            alert.clock_display or "--:--", alert.period_display,
        )
        if emailer.send_alert(alert, cfg):
            state.mark_alerted(game_id)
        else:
            log.error("Alert email for game %s failed; will retry next poll", game_id)


def run() -> int:
    if not config.is_enabled():
        log.info("ENABLED=false — kill switch active, exiting")
        return 0
    try:
        cfg = config.email_config()  # fail loudly now, not at alert time
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 1

    state = StateStore(config.STATE_FILE)
    started = time.monotonic()
    consecutive_failures = 0

    while True:
        now = now_pt()
        if time.monotonic() - started > config.MAX_RUNTIME_SECONDS:
            log.info("Runtime cap reached; exiting — the next scheduled run resumes")
            return 0
        if in_dead_zone(now):
            log.info("Past hard cutoff (%02d:%02d PT); exiting", *config.HARD_CUTOFF_PT)
            return 0

        try:
            data = nba_client.fetch_scoreboard(retries=2)
            consecutive_failures = 0
        except nba_client.FetchError:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error(
                    "Feed unreachable after %d consecutive attempts; giving this run up "
                    "(next scheduled run retries within 30 minutes)",
                    consecutive_failures,
                )
                return 1
            time.sleep(FAILURE_SLEEP_SECONDS)
            continue

        games = data["scoreboard"]["games"]
        if not games:
            log.info("No games on today's scoreboard (idle day or off-season); exiting")
            maybe_heartbeat(state)
            return 0

        live = [g for g in games if g.get("gameStatus") == GAME_STATUS_LIVE]
        pending = [g for g in games if g.get("gameStatus") == GAME_STATUS_SCHEDULED]

        # Alert checks run on every poll, so a game already past the trigger
        # when this process starts still fires immediately (spec section 9).
        process_alerts(live, state, cfg)

        if not live and not pending:
            log.info("All %d games final; exiting for the night", len(games))
            maybe_heartbeat(state)
            return 0

        if not live:
            tip = next_tip_off(pending)
            if tip is None:
                log.warning("Scheduled games with unparseable tip times; polling anyway")
            else:
                wait_minutes = (tip - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
                if wait_minutes > config.PREGAME_LEAD_MINUTES:
                    log.info(
                        "Next tip-off %s PT is %d min away; exiting — a later scheduled run covers it",
                        tip.astimezone(config.PACIFIC).strftime("%H:%M"), int(wait_minutes),
                    )
                    return 0
            if past_soft_cutoff(now):
                log.info("Past soft cutoff with no live games; exiting")
                return 0
            time.sleep(config.POLL_INTERVAL_IDLE)
        else:
            time.sleep(config.POLL_INTERVAL_LIVE)


def simulate(fixture_path: str | None = None) -> int:
    """Replay a fixture through the full pipeline and send a real test email.

    Proves trigger logic, dedup, formatting, and SMTP delivery end-to-end
    before the season starts (spec section 11). Uses a throwaway state file so
    real dedup state is untouched.
    """
    import tempfile
    from pathlib import Path

    path = Path(fixture_path or Path(__file__).parent.parent / "tests" / "fixtures" / "close_game_q4.json")
    log.info("Simulating from fixture: %s", path)
    with open(path, encoding="utf-8") as f:
        games = json.load(f)["scoreboard"]["games"]

    try:
        cfg = config.email_config()
    except config.ConfigError as exc:
        log.error("%s — set GMAIL_ADDRESS / GMAIL_APP_PASSWORD / ALERT_RECIPIENT first", exc)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        state = StateStore(str(Path(tmp) / "state.json"))
        sent = 0
        for game in games:
            alert = evaluate_game(game)
            label = f"{game.get('awayTeam', {}).get('teamTricode')} @ {game.get('homeTeam', {}).get('teamTricode')}"
            if alert is None:
                log.info("%s: no alert (status=%s period=%s clock=%r)",
                         label, game.get("gameStatus"), game.get("period"), game.get("gameClock"))
                continue
            log.info("%s: TRIGGER (%s) — sending test email to %s", label, alert.reason, cfg.recipient)
            if emailer.send_alert(alert, cfg, subject_prefix="[TEST] "):
                state.mark_alerted(alert.game_id)
                sent += 1
            else:
                log.error("Test email failed — check Gmail credentials/app password")
                return 1

        # Prove dedup: a second pass over the same fixture must send nothing.
        for game in games:
            game_id = str(game.get("gameId", ""))
            if evaluate_game(game) is not None and not state.is_alerted(game_id):
                log.error("Dedup failure: game %s would have re-alerted", game_id)
                return 1

    if sent == 0:
        log.warning("Fixture produced no alerts — nothing sent (try close_game_q4.json)")
        return 1
    log.info("Simulation complete: %d test email(s) sent, dedup verified", sent)
    return 0


def check_schedule() -> int:
    """Fetch the real scoreboard and report what the runner would do now."""
    data = nba_client.fetch_scoreboard()
    games = data["scoreboard"]["games"]
    log.info("Scoreboard date: %s — %d game(s)", data["scoreboard"].get("gameDate"), len(games))
    for game in games:
        tip = parse_tip_time(game)
        tip_pt = tip.astimezone(config.PACIFIC).strftime("%H:%M PT") if tip else "?"
        log.info(
            "  %s @ %s — status=%s period=%s clock=%r tip=%s",
            game.get("awayTeam", {}).get("teamTricode"),
            game.get("homeTeam", {}).get("teamTricode"),
            game.get("gameStatusText"), game.get("period"), game.get("gameClock"), tip_pt,
        )
    if not games:
        log.info("Runner would exit immediately (no games today)")
    return 0
