# Project Context

- **Owner:** drdonoso
- **Project:** SoccerGoals — A background process that triggers on soccer match goals, retrieves media video from Reddit based on a title template
- **Stack:** Python 3.12+ async (httpx, asyncpraw, aiosqlite, python-telegram-bot, yt-dlp)
- **Created:** 2026-04-15

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

### Full implementation — 2026-04-15

**Files created (10 total under `src/soccergoals/`):**
- `pyproject.toml` — setuptools-based, pip-compatible (no poetry). Deps: httpx, asyncpraw, aiosqlite, python-telegram-bot, yt-dlp.
- `models.py` — Four dataclasses: GoalEvent, RedditPost, DownloadResult, SendResult.
- `config.py` — All config from `os.environ`. Fail-fast on missing required vars. Parses MONITORED_TEAMS as comma-separated list.
- `poller.py` — Async httpx client hitting API-Football `/fixtures?live=all`. In-memory tracking of known goals per fixture. Fuzzy team matching via `difflib.SequenceMatcher` (threshold 0.65) + substring containment.
- `searcher.py` — asyncpraw search on r/soccer with keyword query (surname + both teams). Extracts streamff.link URLs via regex, with fallback to other video hosts. Filters by MAX_POST_AGE_MINUTES.
- `downloader.py` — Two-path strategy: (1) direct HTTP download for streamff.link (parse page for `<source src=...>` video URL), (2) yt-dlp subprocess fallback. Stream download to temp dir.
- `sender.py` — python-telegram-bot `Bot.send_video()`. Enforces 50MB limit. Cleans up temp file in `finally` block.
- `store.py` — aiosqlite with `processed_goals` + `poll_state` tables. SHA-256 event_hash for dedup. UPSERT via `ON CONFLICT`. Retry query filters by status + retry_count.
- `main.py` — Orchestrator with async event loop: poll → search → download → send → record. Exponential backoff retries (2^n * polling_interval). Graceful SIGTERM/SIGINT handling. Structured logging to stdout.
- `__main__.py` — Enables `python -m soccergoals` in addition to `python -m soccergoals.main`.

**Key patterns:**
- Event dedup: `sha256(match_id:scorer:minute)` — stored in SQLite `event_hash` column.
- Retry backoff: time-based comparison against `updated_at` — waits `2^retry_count * polling_interval` seconds.
- Poller keeps in-memory `_known_goals` dict per fixture to avoid re-emitting goals within the same process lifetime.
- Temp file cleanup: after Telegram send (`finally` block in sender) + sweep on shutdown.
- `__main__.py` delegates to `main()` — Dockerfile ENTRYPOINT uses `python -m soccergoals.main`.
