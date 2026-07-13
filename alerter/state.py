"""Dedup state: which games have already been emailed, ever.

A JSON file committed back to the repo (when GIT_COMMIT_STATE=true, i.e. in
GitHub Actions) so it survives process restarts and fresh checkouts. Locally
it is just a file. Game IDs are pruned after 60 days — NBA game IDs are
unique across seasons, so pruning only keeps the file small (spec section 8).
"""

import datetime
import json
import logging
import os
import subprocess
import tempfile

log = logging.getLogger(__name__)

PRUNE_AFTER_DAYS = 60


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.commit_enabled = os.environ.get("GIT_COMMIT_STATE", "").lower() == "true"
        self._data = {"alerted": {}, "last_heartbeat": None}
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded.get("alerted"), dict):
                self._data["alerted"] = loaded["alerted"]
            self._data["last_heartbeat"] = loaded.get("last_heartbeat")
        except FileNotFoundError:
            log.info("No state file at %s yet; starting fresh", path)
        except (ValueError, OSError) as exc:
            # A corrupt state file must not kill the night's polling; worst
            # case is one duplicate email per game, which beats zero emails.
            log.error("Could not read state file %s (%s); starting fresh", path, exc)

    # -- dedup ---------------------------------------------------------------

    def is_alerted(self, game_id: str) -> bool:
        return game_id in self._data["alerted"]

    def mark_alerted(self, game_id: str) -> None:
        today = datetime.date.today()
        self._data["alerted"][game_id] = today.isoformat()
        self._prune(today)
        self._save(f"alert: game {game_id} ({today.isoformat()})")

    def _prune(self, today: datetime.date) -> None:
        cutoff = today - datetime.timedelta(days=PRUNE_AFTER_DAYS)
        kept = {}
        for game_id, date_str in self._data["alerted"].items():
            try:
                if datetime.date.fromisoformat(date_str) >= cutoff:
                    kept[game_id] = date_str
            except ValueError:
                kept[game_id] = date_str  # keep entries we can't date
        self._data["alerted"] = kept

    # -- heartbeat (off-season liveness + keeps scheduled workflows active) --

    def heartbeat_due(self, interval_days: int) -> bool:
        last = self._data.get("last_heartbeat")
        if not last:
            return True
        try:
            last_date = datetime.date.fromisoformat(last[:10])
        except ValueError:
            return True
        return (datetime.date.today() - last_date).days >= interval_days

    def record_heartbeat(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self._data["last_heartbeat"] = now
        self._save(f"heartbeat: alive {now[:10]}")

    # -- persistence ---------------------------------------------------------

    def _save(self, commit_message: str) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        # Atomic write: never leave a truncated JSON file behind.
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        if self.commit_enabled:
            self._commit_and_push(commit_message)

    def _commit_and_push(self, message: str) -> None:
        """Commit and push the state file so dedup survives across runs.

        Never raises: dedup for the current run lives in memory regardless;
        a push failure only risks a duplicate email after a restart, and the
        next successful save will carry this change along.
        """
        def git(*args, check=True):
            return subprocess.run(
                ["git", *args], check=check, capture_output=True, text=True, timeout=60
            )

        try:
            git("add", self.path)
            result = git("commit", "-m", message, check=False)
            if result.returncode != 0:
                if "nothing to commit" in (result.stdout + result.stderr):
                    return
                log.error("git commit failed: %s", result.stderr.strip())
                return
            for attempt in range(3):
                push = git("push", check=False)
                if push.returncode == 0:
                    log.info("State committed and pushed: %s", message)
                    return
                # Another run (or a queued cron) pushed first; rebase and retry.
                log.warning("git push failed (attempt %d): %s", attempt + 1, push.stderr.strip())
                git("pull", "--rebase", check=False)
            log.error("Could not push state after retries; dedup persists in-memory for this run only")
        except (subprocess.SubprocessError, OSError) as exc:
            log.error("git state persistence failed: %s", exc)
