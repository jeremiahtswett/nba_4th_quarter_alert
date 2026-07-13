"""Pure close-game trigger logic (spec section 2).

Alert when a live game is in Q4 with < 5:00 on the clock and a score
differential of at most 5 points, or when a live game reaches overtime.
Deduplication ("one email per game, ever") is the caller's job — this module
only answers: does this game, as it stands right now, warrant an alert?
"""

from dataclasses import dataclass
from typing import Optional

from .clock import format_clock, parse_game_clock
from .config import CLOSE_GAME_MAX_DIFF, Q4_TRIGGER_SECONDS

GAME_STATUS_SCHEDULED = 1
GAME_STATUS_LIVE = 2
GAME_STATUS_FINAL = 3

REASON_CLOSE_Q4 = "close-q4"
REASON_OVERTIME = "overtime"


@dataclass
class Alert:
    game_id: str
    reason: str
    away_tricode: str
    home_tricode: str
    away_score: int
    home_score: int
    period: int
    clock_display: str  # "3:42", or "" when the clock is momentarily empty
    series_text: str

    @property
    def matchup(self) -> str:
        return f"{self.away_tricode} @ {self.home_tricode}"

    @property
    def period_display(self) -> str:
        if self.period <= 4:
            return f"Q{self.period}"
        if self.period == 5:
            return "OT"
        return f"{self.period - 4}OT"


def period_of(game: dict) -> int:
    try:
        return int(game.get("period") or 0)
    except (TypeError, ValueError):
        return 0


def _score_of(team: dict) -> int:
    try:
        return int(team.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_game(game: dict) -> Optional[Alert]:
    """Return an Alert if this game currently meets the trigger, else None."""
    if game.get("gameStatus") != GAME_STATUS_LIVE:
        return None

    period = period_of(game)
    seconds_left = parse_game_clock(game.get("gameClock"))
    home = game.get("homeTeam") or {}
    away = game.get("awayTeam") or {}
    home_score = _score_of(home)
    away_score = _score_of(away)

    reason = None
    if period >= 5:
        # Overtime is close by definition; trigger even while the clock is
        # empty between Q4 and OT so the email goes out immediately.
        reason = REASON_OVERTIME
    elif (
        period == 4
        and seconds_left is not None  # empty clock: can't confirm < 5:00
        and seconds_left < Q4_TRIGGER_SECONDS
        and abs(home_score - away_score) <= CLOSE_GAME_MAX_DIFF
    ):
        reason = REASON_CLOSE_Q4

    if reason is None:
        return None

    return Alert(
        game_id=str(game.get("gameId", "")),
        reason=reason,
        away_tricode=str(away.get("teamTricode", "???")),
        home_tricode=str(home.get("teamTricode", "???")),
        away_score=away_score,
        home_score=home_score,
        period=period,
        clock_display=format_clock(seconds_left) if seconds_left is not None else "",
        series_text=str(game.get("seriesText") or ""),
    )
