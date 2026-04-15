# Squad Decisions

## Active Decisions

### SoccerGoals Architecture
- **Author:** Leela
- **Date:** 2026-04-15
- **Status:** Proposed (awaiting drdonoso input)

Proposed the foundational architecture for SoccerGoals — Python 3.12+ async process polling API-Football for live goals, searching Reddit (r/soccer) via asyncpraw, and downloading media via yt-dlp. SQLite for state management. Key choices: single async process, TOML config, clear component separation (Match Poller, Reddit Searcher, Media Downloader, State Store, Orchestrator). Open items: league scope, storage strategy, notifications, hosting, retention policy. See `docs/architecture.md`.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
