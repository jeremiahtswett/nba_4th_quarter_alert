"""Configuration: environment variables and tuning constants."""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

SCOREBOARD_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"

PACIFIC = ZoneInfo("America/Los_Angeles")

# Trigger thresholds (spec section 2).
Q4_TRIGGER_SECONDS = 300.0  # alert when strictly less than 5:00 remains in Q4
CLOSE_GAME_MAX_DIFF = 5     # ... and the score differential is at most 5

# Polling cadence (spec section 5).
POLL_INTERVAL_LIVE = 30   # seconds between polls while any game is live
POLL_INTERVAL_IDLE = 60   # seconds between polls while waiting for a tip-off

# If the next tip-off is further out than this, exit and let a later
# scheduled run (crons fire every 30 minutes) pick up the window instead of
# holding a runner idle.
PREGAME_LEAD_MINUTES = 40

# Nightly window, in Pacific Time. After the soft cutoff we only keep polling
# if a game is still live; the hard cutoff stops polling unconditionally so a
# hung feed can't pin a runner all night.
SOFT_CUTOFF_PT = (23, 30)  # 11:30 PM PT
HARD_CUTOFF_PT = (1, 0)    # 1:00 AM PT (safety cap for multi-OT late games)

# Exit before GitHub Actions' 6-hour job cap; the next queued cron resumes.
MAX_RUNTIME_SECONDS = 5.5 * 3600

HEARTBEAT_INTERVAL_DAYS = 7

STATE_FILE = os.environ.get("STATE_FILE", "state/alerted_games.json")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class ConfigError(Exception):
    pass


@dataclass
class EmailConfig:
    sender: str
    app_password: str
    recipient: str


def is_enabled() -> bool:
    """Kill switch (spec section 5). Defaults to enabled."""
    return os.environ.get("ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def email_config() -> EmailConfig:
    """Read Gmail credentials from the environment; fail loudly if missing."""
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("ALERT_RECIPIENT", "").strip() or sender
    missing = [name for name, val in (("GMAIL_ADDRESS", sender), ("GMAIL_APP_PASSWORD", password)) if not val]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
    return EmailConfig(sender=sender, app_password=password, recipient=recipient)
