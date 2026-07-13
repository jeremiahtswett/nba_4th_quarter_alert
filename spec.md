# NBA Close-Game Email Alerter — Project Spec

## 1. Goal

Build a fully automated system that emails me when an NBA game becomes "close" late in the 4th quarter, so I can tune in for the best endings without watching full games. Once deployed, it must run itself — zero manual intervention on game nights, across the entire season.

## 2. Definition of a "Close Game" (the core trigger)

Alert when ALL of the following are true for a live game:

- Game is in the **4th quarter** (`period == 4`)
- **Less than 5:00 remaining** on the game clock
- **Score differential ≤ 5 points** (absolute value)

**Recommended addition (implement unless it adds meaningful complexity):** also alert for any game entering **overtime**, since OT games are close by definition. Dedup rules below still apply — one email per game, ever.

## 3. Alerting Rules

- **One email per game, ever.** If a game triggers an alert, never email about that game again — even if the lead balloons to 10 and shrinks back to 2. State must persist across polls (and across process restarts) to guarantee this.
- Email content should include: matchup (e.g., "LAL @ BOS"), current score, game clock, and period.
- Subject line should be scannable on a phone lock screen, e.g., `🚨 Close game: LAL 98 – BOS 101, 3:42 left in Q4`.

## 4. Data Source

You have full latitude to choose the best live-scoreboard data source. Requirements: no paid API keys, no auth, reasonably real-time (updates at least every ~30s during live play), and it must expose period, game clock, and both teams' scores for every live game.

**Known-good default (verified working):** the free public NBA scoreboard JSON feed below returns all of today's games with live period/clock/score. Evaluate it first; only move to an alternative if you find one that is meaningfully more reliable or richer.

```
https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json
```

I used exactly this endpoint in a previous (AWS) version of this project and it worked perfectly. The games array lives at `scoreboard.games[]`. Note: hitting this URL over plain `http://` in a browser returns an Akamai "Access Denied" page — that is the gotcha below, not a dead endpoint. Fetched correctly (see below), it returns valid JSON.

**CRITICAL GOTCHA — do not skip this.** This feed sits behind Akamai and will return an HTML **"Access Denied"** page (not JSON) if you request it incorrectly. Two things cause the denial:
1. **Plain HTTP** — you must use `https://`, never `http://`.
2. **Missing/blank User-Agent** — you must send a browser-like `User-Agent` header (and it's safest to also send an `Accept: application/json` and a `Referer` of the NBA site). A bare `requests.get(url)` with the default python-requests UA can be blocked.

Always validate that the response is actually JSON (check content-type / attempt a parse) and treat an HTML body as a failed fetch to be retried, not as valid data.

**Fields you'll need per game** (exact key names/nesting may vary by source — confirm against a live payload):
- A unique **game ID** → use for dedup state.
- A **game status** distinguishing scheduled / live / final.
- **Period** (integer; 4 = 4th quarter, 5+ = overtime).
- **Game clock** — often an ISO 8601 duration string like `PT04M32.00S`. **Parse carefully**; it is an **empty string** for games that aren't actively playing (scheduled, halftime, between quarters). Confirmed behavior in the known-good feed.
- **Both teams' scores.**
- **Team tricodes** (e.g., `LAL`, `BOS`) for the email matchup line.
- **Scheduled start time (UTC)** — useful for schedule-aware polling.
- Playoff/series context fields (e.g., a series label) may exist and are nice-to-have for email content.

## 5. Polling Strategy

Poll every **~30 seconds** while games are live, but be smart about *when* to poll at all:

**Preferred approach — schedule-aware polling:**
1. Once per day (e.g., early afternoon PT), fetch the scoreboard to get today's game schedule.
2. If no games today → do nothing until tomorrow. **This automatically handles the off-season** (season starts late October; the system should sit idle until games appear, with no manual toggling needed).
3. If games exist, begin the 30-second polling loop shortly before the first tip-off and continue until all games are final (`gameStatus == 3`) or a hard cutoff (~11:30 PM PT) is reached.
4. Optimization (optional): only poll intensively for games that are in Q3/Q4; you can slow the poll rate when all live games are early or blowouts.

**Fallback approach (acceptable if simpler on the chosen platform):** fixed polling window of roughly 4:00 PM–11:30 PM PT daily (weekend games can tip off as early as ~12 PM PT — consider covering those), skipping the loop entirely when the scoreboard shows no games.

**Manual override:** include a simple `ENABLED` flag (env var or config file) as a kill switch, but the default behavior should be self-managing via the schedule.

## 6. Email Delivery

- **Gmail SMTP** with an App Password (I've used this pattern before in another project — Python `smtplib`, TLS on port 587).
- Credentials (Gmail address, app password, recipient address) must come from **environment variables / platform secrets**, never hardcoded or committed.
- Include setup instructions in the README for generating a Gmail App Password.

## 7. Hosting / Execution Platform — YOUR DECISION

You choose where this runs. Hard requirements:

- **Fully automatic.** I never manually start it on game nights.
- **~$0/month** (free tier or negligible cost).
- Reliable during the polling window — a missed alert defeats the whole purpose.
- Supports persistent dedup state across polls and restarts.

Options to evaluate (not exhaustive — pick the best fit and justify it briefly in the README):

- **GitHub Actions**: free, I already use it for another automation. Caveats: scheduled cron has ~5-min minimum granularity and unreliable start times, and jobs max out at 6 hours. A viable pattern is one scheduled workflow that launches a long-running job which loops internally with 30s sleeps for the duration of the game window; state persisted via cache, artifacts, a committed state file, or a gist. Evaluate whether scheduling reliability is acceptable.
- **Always-on free-tier compute** (e.g., Oracle Cloud free tier, Fly.io, a small VPS): simplest architecture — a single Python process with an internal scheduler (or cron + script). Most reliable for 30s polling.
- **AWS Lambda + EventBridge**: my previous solution used this. EventBridge minimum rate is 1 minute, so 30s polling requires a workaround (e.g., poll twice per invocation with a 30s sleep). Fine if it's the best overall option, but I'm not attached to it.

Weigh reliability > simplicity > my familiarity > cost (all should be ~free anyway). Document the tradeoff you made.

## 8. State Management

Keep it minimal — no real database:

- `alerted_game_ids` — set of gameIds already emailed (scoped per season or pruned by date; a game from last night never re-alerts anyway since gameIds are unique, so a simple append-only store pruned occasionally is fine).
- Must survive process restarts and platform redeploys.
- A JSON file, key-value store, gist, or committed file — whatever fits the chosen platform.

## 9. Reliability & Edge Cases

- The data source occasionally returns errors or stale data. A failed fetch must **not** crash the night's polling loop — log, back off briefly, retry.
- **Guard against silent HTML "Access Denied" responses** (see section 4): a 200 status can still carry an Akamai denial page instead of JSON. Verify the body parses as JSON before trusting it; treat non-JSON as a failed fetch and retry, and log loudly if it persists (it usually means the User-Agent/HTTPS requirement regressed).
- `gameClock` can be an empty string (between quarters, halftime). Handle gracefully.
- Timezones: the endpoint reports times in UTC/ET; my window logic is in **Pacific Time**. Be explicit about timezone conversions.
- If email sending fails, retry at least once before giving up; log failures visibly.
- Double-check that a game already past the trigger when polling starts (e.g., process restarted at 2:00 left in a 3-point game) still fires an alert immediately.

## 10. Tech Stack Preferences

- **Python** preferred (matches my existing tooling), but if the chosen platform strongly favors something else (e.g., Node), that's acceptable — justify it.
- Minimal dependencies. Standard library + `requests` should nearly cover it.
- Clean, readable code with a short README covering: architecture decision + rationale, secrets setup, how to deploy, how to test, and how the season on/off behavior works.

## 11. Testing & Verification (important — the season hasn't started)

The season starts in late October, so live games aren't available now. Build so I can verify it works today:

- Unit tests for the trigger logic (close-game detection, gameClock parsing, dedup) using **saved/mocked JSON fixtures** — include fixtures for: live close game in Q4, live blowout, game in Q2, halftime (empty clock), OT, final game, no games today.
- A **dry-run / simulation mode** that replays a fixture through the full pipeline and sends a real test email, so I can confirm end-to-end delivery works before opening night.
- A way to confirm the scheduler is alive during the off-season (e.g., a log line or optional weekly heartbeat).

## 12. Non-Goals

- No web UI or dashboard.
- No SMS/push notifications (email only, for now — but don't architect in a way that makes adding push painful later).
- No historical stats, standings, or anything beyond the alert.
- No support for other leagues.

## 13. Success Criteria

1. On a night with games, the system starts polling by itself, detects any game meeting the close-game criteria, and emails me within ~60 seconds of the condition becoming true.
2. Exactly one email per qualifying game, ever.
3. Zero emails and near-zero resource usage on days with no games (including the entire off-season).
4. Runs for a full season without my intervention.
5. I can verify all of the above today via tests and simulation mode, before the season starts.

## 14. Your Flexibility

The architecture above reflects what worked in my previous AWS build, but you have latitude: if you identify a meaningfully better data source, polling strategy, hosting platform, or state mechanism that still satisfies sections 2, 3, 6, 7, and 13, propose it briefly (2–3 sentences of tradeoffs) and proceed with the better option. Optimize for reliability and zero-maintenance over cleverness.
