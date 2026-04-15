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
