# Project Context

- **Owner:** drdonoso
- **Project:** SoccerGoals — A background process that triggers on soccer match goals, retrieves media video from Reddit based on a title template
- **Stack:** Python 3.12+ async (httpx, asyncpraw, aiosqlite, python-telegram-bot, yt-dlp)
- **Created:** 2026-04-15

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

### Reddit-first rewrite (Option A) — 2026-04-15

**Scope:** Full codebase rewrite to eliminate Football API dependency. Reddit is now the sole data source.

**Files deleted (2):**
- `poller.py` — Match Poller removed entirely
- `searcher.py` — Reddit Searcher removed (merged into scanner)

**Files created (1):**
- `scanner.py` — RedditGoalScanner: browses r/soccer/new via asyncpraw, parses goal titles with GOAL_TITLE_PATTERN regex, fuzzy-matches monitored teams (with alias map), extracts streamff.link URLs. Returns `list[ScanResult]`.

**Files updated (9):**
- `models.py` — GoalEvent simplified (removed match_id, assist, scoring_team, aggregate; added event_id). Added ScanResult dataclass.
- `config.py` — Removed FOOTBALL_API_KEY. Changed POLLING_INTERVAL_SECONDS default from 45 to 30.
- `store.py` — Replaced poll_state table with seen_posts table. New two-layer dedup: post_id (layer 1) + event_hash with normalized team names/surname (layer 2). Updated all methods to use event_id + (home_team, away_team, scorer, minute) signature. Hash now 16 chars, uses unicodedata for accent stripping.
- `main.py` — Orchestrator rewired: scanner replaces poller+searcher. New _tick: scan → dedup (post_id then event_hash) → download → send → record. Retry re-scans r/soccer/new.
- `downloader.py` — Uses event_id instead of match_id for temp file naming.
- `sender.py` — No code changes needed (caption format already correct).
- `docker-compose.yml` — Polling default changed to 30.
- `Dockerfile` — Entrypoint changed to `python -m soccergoals`.
- `README.md` — Rewritten: 3-step pipeline (Scan→Download→Send), removed all Football API references.
- `__main__.py` — Added `if __name__ == "__main__"` guard.

**Tests updated (6 files):**
- Deleted `test_poller.py`, `test_searcher.py`
- Created `test_scanner.py` (24 tests: title regex, team normalization, fuzzy matching, media extraction, event ID, full scanner integration)
- Updated `conftest.py`, `test_config.py`, `test_models.py`, `test_store.py`, `test_downloader.py`
- All 78 tests pass.

**Key patterns:**
- Event dedup: `sha256(normalize(home_team)|normalize(away_team)|normalize(scorer_surname)|minute)[:16]`
- Team alias map for common abbreviations (Barça→Barcelona, Atleti→Atletico Madrid, etc.)
- Two-layer dedup: seen_posts table (post_id) + processed_goals table (event_hash)

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
