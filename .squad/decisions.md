# Squad Decisions

## Active Decisions

### SoccerGoals Architecture
- **Author:** Leela
- **Date:** 2026-04-15
- **Status:** Proposed (awaiting drdonoso input)

Proposed the foundational architecture for SoccerGoals — Python 3.12+ async process polling API-Football for live goals, searching Reddit (r/soccer) via asyncpraw, and downloading media via yt-dlp. SQLite for state management. Key choices: single async process, TOML config, clear component separation (Match Poller, Reddit Searcher, Media Downloader, State Store, Orchestrator). Open items: league scope, storage strategy, notifications, hosting, retention policy. See `docs/architecture.md`.

### Scope Decisions — Confirmed by drdonoso
- **Author:** Leela (Lead)
- **Date:** 2026-04-15
- **Status:** Confirmed

Five scope decisions confirmed:
1. **Reddit Source:** r/soccer only — hardcoded, no multi-subreddit support.
2. **Title Format:** `{home_team} [{home_score}] - {away_score} {away_team} [{aggregate}] - {scorer} {minute}'` — `[X]` brackets mark scoring team.
3. **Media Source:** streamff.link primary, yt-dlp fallback.
4. **Output Destination:** Telegram channel via python-telegram-bot. Config: `TELEGRAM_BOT_TOKEN` env var + `channel_id` in config.toml.
5. **Match Filtering:** Team-based (`[teams] monitored` list) replaces league-based filtering.

Impact: Telegram Sender added, GoalEvent model updated, RedditSearcher simplified, config restructured. Open questions reduced to 3 (hosting, retention, tooling).

### r/soccer Title Format Variants
- **Author:** Leela (Lead)
- **Date:** 2026-04-15
- **Requested by:** drdonoso
- **Status:** Accepted

Both r/soccer title formats are valid (with and without score brackets). Scoring team attribution comes from API-Football, not Reddit titles. Search queries are keyword-based (scorer name, team names, score digits) — no bracket-specific patterns. Future title parser must accept both variants. See `docs/architecture.md`.

### Docker Hosting with Env Var Config
- **Author:** Leela (Lead)
- **Date:** 2026-04-15
- **Decided by:** drdonoso
- **Status:** Confirmed

Docker via docker-compose for hosting. All configuration driven by environment variables in docker-compose.yml (twelve-factor). SQLite persisted via volume mount (`./data:/app/data`). Single container with `restart: unless-stopped`. No config.toml or separate config files. Files created: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `README.md`; updated `docs/architecture.md`.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
