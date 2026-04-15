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

### Implementation Decisions
- **Author:** Fry (Backend Dev)
- **Date:** 2026-04-15
- **Status:** Applied

Seven implementation decisions made during full system build: setuptools over poetry for pip-compatible builds; in-memory goal tracking in poller with SQLite dedup as fallback; keyword-based Reddit search using scorer surname + team names; streamff page parsing via regex with yt-dlp fallback; time-based exponential backoff for retries; DB_PATH and TEMP_DIR env vars for flexible paths; Telegram cleanup in `finally` block to prevent disk buildup.

### Fix aiosqlite Dependency Conflict
- **Author:** Hermes (Tester)
- **Date:** 2026-04-15
- **Status:** Applied

Changed `aiosqlite>=0.20` to `aiosqlite<=0.17` in pyproject.toml to resolve conflict with asyncpraw's `aiosqlite<=0.17.0` requirement. No code changes needed — aiosqlite 0.17 API covers all usage in `store.py`. All 77 tests pass.

### Reddit-First Architecture Pivot (Option A)
- **Author:** Leela (Lead)
- **Date:** 2026-04-15
- **Decided by:** drdonoso
- **Status:** Confirmed

Football API eliminated. Reddit is the sole external data source. The Reddit Goal Scanner replaces both the Match Poller and Reddit Searcher — browses r/soccer/new every ~30s, parses titles via regex, filters monitored teams, extracts streamff.link URLs. GoalEvent model simplified (removed match_id, scoring_team, aggregate, assist). Two-layer dedup: post_id + event_hash (normalized). Pipeline reduced to 3 components. FOOTBALL_API_KEY removed. Full rewrite of `docs/architecture.md`.

### Reddit-First Rewrite Implementation
- **Author:** Fry (Backend Dev)
- **Date:** 2026-04-15
- **Status:** Applied

Full code rewrite implementing Option A. Deleted `poller.py` and `searcher.py`, created `scanner.py` (RedditGoalScanner). Team alias map hardcoded in scanner.py (common abbreviations: Barça, Atleti, Spurs, etc.). Event hash uses surname-only normalization — two same-surname scorers at the same minute would dedup (extremely unlikely). Retry re-scans r/soccer/new and matches by scorer+minute (may miss aged-out posts). `__main__.py` guard added. 78 tests passing, no new dependencies.

### Dockerfile Two-Stage Install Pattern
- **Author:** Bender (DevOps)
- **Date:** 2026-04-15
- **Status:** Implemented

Two-stage pip install in Dockerfile: first stage copies `pyproject.toml` with a stub package to cache dependencies, second stage copies real `src/` and reinstalls with `--no-deps`. Fixes build failure where setuptools couldn't discover packages without `src/`. Faster iterative builds for source-only changes. No runtime behavior changes.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
