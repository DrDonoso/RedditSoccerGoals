# Project Context

- **Owner:** drdonoso
- **Project:** SoccerGoals — A background process that triggers on soccer match goals, retrieves media video from Reddit based on a title template
- **Stack:** TBD (to be decided by team)
- **Created:** 2026-04-15

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

### 2026-04-15 — Initial Architecture
- Proposed Python 3.12+ stack: asyncpraw, yt-dlp, httpx, aiosqlite (SQLite)
- Architecture: single async polling loop with 4 core components (Poller, Searcher, Downloader, Store)
- Goal detection via API-Football (free tier, 100 req/day, ~1 min delay)
- Reddit search uses configurable title template against r/soccer
- Media extraction via yt-dlp (supports streamable, v.redd.it, etc.)
- State/dedup via SQLite with event hash (`match_id + scorer + minute`)
- Key file: `docs/architecture.md`
- Decision filed: `.squad/decisions/inbox/leela-architecture.md`
- Open questions flagged: league scope, storage strategy, notifications, hosting, retention, tooling prefs
- drdonoso needs to confirm stack and answer open questions before Fry starts building

### 2026-04-15 — Reddit-First Architecture Pivot (Option A)
- drdonoso approved: football API eliminated entirely, Reddit is sole data source
- Match Poller killed, Reddit Searcher replaced by Reddit Goal Scanner
- Scanner browses r/soccer/new every ~30s, parses titles with regex, filters monitored teams, extracts streamff.link URLs
- GoalEvent model simplified: removed match_id, scoring_team, aggregate, assist; added derived event_id
- Two-layer dedup: post_id (fast skip) + event_hash (semantic dedup via normalized team+scorer+minute hash)
- Pipeline: Reddit Goal Scanner → Media Downloader → Telegram Sender (3 components, was 4)
- Removed FOOTBALL_API_KEY from docker-compose.yml and config table
- Full rewrite of `docs/architecture.md`
- Decision filed: `.squad/decisions/inbox/leela-reddit-first.md`

### 2026-04-15 — Scope Decisions Confirmed
- Reddit source locked to r/soccer only (hardcoded, not configurable)
- Title format confirmed as actual r/soccer convention: `{home_team} [{home_score}] - {away_score} {away_team} [{aggregate}] - {scorer} {minute}'`
- Media source: streamff.link is primary target, yt-dlp is fallback
- Output destination: Telegram channel (not local storage) — new TelegramSender component added
- Match filtering: team-based (monitored teams list), NOT league-based
- GoalEvent model updated: added `scoring_team`, `aggregate`; removed `league`
- Architecture doc fully updated, 4 of 7 open questions resolved (leagues, storage, notifications, title format)
- Remaining open: hosting, retention, tooling prefs
- Decision filed: `.squad/decisions/inbox/leela-scope-decisions.md`

### 2026-04-15 — Title Format Variants
- r/soccer title format is NOT always consistent — brackets around the score are optional
- Two known variants: with brackets (`[1]`) and without brackets (`1`)
- Scoring team detection must rely on `GoalEvent.scoring_team` from the football API, never on Reddit title brackets
- Reddit search queries should be keyword-based (scorer + teams + score) rather than bracket-format-dependent

### 2026-04-21 — Version Management Strategy
- Recommended git-tag-driven semver with `setuptools-scm` — zero ceremony, git tags are the single source of truth
- `pyproject.toml` version becomes dynamic (no hardcoded string to maintain)
- Versions bump only on explicit `git tag -a vX.Y.Z` + push, NOT on every push to main
- Every push to main still deploys `latest` Docker tag (no change to current behavior)
- Rejected python-semantic-release (overkill for hobby project) and bump2version (unmaintained)
- CI workflow needs: extract version from git tag, add versioned Docker tag on tag-push events
- Key files: `.github/workflows/docker-deploy.yml`, `pyproject.toml`
- Decision filed: `.squad/decisions/inbox/leela-versioning-strategy.md`
- Architecture doc updated; decision filed: `.squad/decisions/inbox/leela-title-format-variants.md`

### 2026-04-15 — Docker Hosting & Env Var Config
- drdonoso confirmed: project runs with Docker, config via docker-compose env vars, no config.toml
- Created: `Dockerfile` (Python 3.12-slim, non-root user, ffmpeg + yt-dlp)
- Created: `docker-compose.yml` (single service, all config as env vars, volume for SQLite, `restart: unless-stopped`)
- Created: `.dockerignore`, `README.md` (quick start guide)
- Updated: `docs/architecture.md` — tech stack table (Docker hosting), config section (env vars replace TOML), directory structure added, error handling (container restart policy)
- Decision filed: `.squad/decisions/inbox/leela-docker-hosting.md`
