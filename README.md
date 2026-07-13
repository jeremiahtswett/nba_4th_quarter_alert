# NBA Close-Game Email Alerter

Emails you when an NBA game gets close late in the 4th quarter — or reaches
overtime — so you can tune in for the best endings. Fully automatic: it polls
on game nights, sleeps through idle days and the entire off-season, and sends
**exactly one email per game, ever**.

**Trigger:** a live game in Q4 with less than 5:00 on the clock and a score
differential of 5 or fewer points, or any game entering overtime.

## Architecture

**Platform: GitHub Actions** (this repo is public so Actions minutes are
unlimited and free).

A scheduled workflow fires every 30 minutes across the nightly window
(~9:00 AM–11:30 PM Pacific). Each run:

1. Fetches the NBA live scoreboard
   (`cdn.nba.com/.../todaysScoreboard_00.json` — free, no auth).
2. **No games today?** Exits in seconds. This is the entire off-season
   behavior — no toggling needed; polling resumes by itself when the schedule
   fills up in late October.
3. **Next tip-off far away?** Exits; a later cron will be running before tip.
4. **Games on?** Polls every 30 seconds, checks every live game against the
   trigger, emails via Gmail SMTP, and records the game ID in
   [`state/alerted_games.json`](state/alerted_games.json), which it commits
   back to the repo so dedup survives restarts and redeploys. The run ends
   when all games are final (or at a hard 1:00 AM PT safety cap for multi-OT
   nights).

### Why GitHub Actions?

Weighing reliability > simplicity > cost (per spec):

- A long-running job that loops internally with 30 s sleeps gives true
  30-second polling with **zero infrastructure** — no VM to patch or get
  reclaimed, no Lambda 1-minute-schedule workaround.
- Actions' known weaknesses are designed around: cron start times are
  unreliable, so the every-30-minute schedule is a **watchdog** — if a run
  dies or starts late, the next cron resumes within ~30 minutes (alerts
  happen late in games, hours after the window opens, so this slack costs
  nothing). Jobs cap at 6 hours, so each run self-terminates at 5.5 h and the
  next queued cron picks up. A `concurrency` group prevents overlapping runs
  from double-polling.
- Scheduled workflows are disabled after 60 days without repo activity; the
  weekly **heartbeat commit** (also your off-season "it's alive" signal)
  keeps that from ever happening.

### Why stdlib `urllib` instead of `requests`?

The scoreboard CDN (Akamai) rejects requests by TLS fingerprint, not just by
User-Agent: with identical browser-like headers, `requests`/urllib3 gets an
HTML "Access Denied" page while stdlib `urllib` gets JSON (verified
empirically). So the fetcher uses `urllib` — which also means **zero
third-party runtime dependencies**. From datacenter IPs (like Actions
runners) the *full* Chrome header set (`Sec-Ch-Ua`, `Sec-Fetch-*`, `Origin`)
is also required — a plain User-Agent gets a 403 (verified from a runner).
The client validates that every response actually parses as scoreboard JSON;
an HTML denial page is treated as a failed fetch, retried, and logged loudly.
If this feed ever regresses permanently, ESPN's public scoreboard
(`site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`) is a
verified-reachable fallback that exposes the same fields.

## Repo layout

```
alerter/
  clock.py       gameClock parsing ("PT04M32.00S" → seconds; "" → None)
  trigger.py     pure close-game/OT decision — no I/O, fully unit-tested
  nba_client.py  scoreboard fetch: browser headers, denial-page guard, retries
  emailer.py     Gmail SMTP (STARTTLS :587), subject/body format, retry
  state.py       dedup store, committed back to the repo from Actions
  runner.py      schedule-aware poll loop, Pacific-time window, heartbeat
  config.py      env vars + tuning constants
.github/workflows/poll.yml   the scheduler (see above)
state/alerted_games.json     game IDs already alerted + last heartbeat
tests/                       unit tests + JSON fixtures for every scenario
```

## Setup

### 1. Gmail App Password

Gmail SMTP requires an app password (your normal password won't work):

1. Enable 2-Step Verification on the Google account:
   https://myaccount.google.com/security
2. Create an app password: https://myaccount.google.com/apppasswords
   (name it e.g. "nba-alerter"). Copy the 16-character password.

### 2. Repository secrets

In the repo: **Settings → Secrets and variables → Actions → New repository
secret** (or with the GitHub CLI):

```bash
gh secret set GMAIL_ADDRESS      # the Gmail account that sends
gh secret set GMAIL_APP_PASSWORD # the 16-char app password
gh secret set ALERT_RECIPIENT    # where alerts go (may equal GMAIL_ADDRESS)
```

### 3. Kill switch (optional)

Polling is on by default. To pause it without touching code, set a repository
**variable** (not secret) named `ENABLED` to `false`:

```bash
gh variable set ENABLED --body false   # pause
gh variable set ENABLED --body true    # resume
```

That's the whole deployment — the schedule in
[`.github/workflows/poll.yml`](.github/workflows/poll.yml) does the rest.

## Testing

### Unit tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

Fixtures cover: close Q4 game (alerts), Q4 blowout, close Q2 game, halftime
(empty clock), overtime, final game, and an empty off-season scoreboard —
plus boundary cases (exactly 5:00 left, 5- vs 6-point diff, restart-past-
trigger, email-failure retry, dedup across restarts).

### End-to-end simulation (sends a real email)

Replays the close-game fixture through the full pipeline — trigger, dedup,
formatting, real Gmail SMTP delivery — with a `[TEST]` subject prefix:

- **In Actions (recommended — also proves your secrets are right):**
  Actions tab → *NBA close-game poller* → **Run workflow** → mode `simulate`.
- **Locally:**

  ```bash
  export GMAIL_ADDRESS=you@gmail.com GMAIL_APP_PASSWORD=xxxx ALERT_RECIPIENT=you@gmail.com
  python -m alerter simulate                     # close-game fixture
  python -m alerter simulate --fixture tests/fixtures/overtime.json
  ```

### See what the runner would do right now

```bash
python -m alerter check-schedule
```

Prints today's real scoreboard and the decision (poll / exit / off-season).

## Season on/off behavior

There is no season switch. Every scheduled run asks the scoreboard "any games
today?" — during the off-season the answer is no and the run exits in
seconds (~2 runner-minutes per day, free). A **weekly heartbeat commit** to
`state/alerted_games.json` confirms the scheduler is alive (check the commit
history) and keeps GitHub from disabling the schedule for inactivity. When
opening night arrives, polling simply starts happening.

## Operational notes

- **One email per game, ever:** enforced by `state/alerted_games.json`,
  committed from the workflow. Entries are pruned after 60 days (game IDs
  never repeat, so pruning is only housekeeping).
- **Email failure:** the send is retried once after 10 s; if it still fails,
  the game is *not* marked alerted, so the next 30 s poll retries.
- **Feed failure:** never crashes the night — failed fetches back off and
  retry; after ~10 consecutive failures the run gives up and the next cron
  (≤30 min) takes over.
- **Timezones:** the feed reports UTC; all window logic converts explicitly
  to `America/Los_Angeles` (DST-safe via `zoneinfo`).
- **Alert email looks like:**
  `🚨 Close game: LAL 98 – BOS 101, 3:42 left in Q4`
