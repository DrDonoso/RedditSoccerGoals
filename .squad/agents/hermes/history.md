# Project Context

- **Owner:** drdonoso
- **Project:** SoccerGoals — A background process that triggers on soccer match goals, retrieves media video from Reddit based on a title template
- **Stack:** TBD (to be decided by team)
- **Created:** 2026-04-15

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

### 2026-04-15 — Initial test suite created
- Wrote 77 tests across 7 test files covering all components: models, config, poller, searcher, downloader, sender, store.
- All external services (API-Football, Reddit, Telegram, streamff.link, yt-dlp) are fully mocked.
- Fixed dependency conflict: `aiosqlite>=0.20` was incompatible with `asyncpraw>=7.7` which requires `aiosqlite<=0.17`. Changed main dependency to `aiosqlite<=0.17`.
- `httpx.AsyncClient.stream()` returns an async context manager, not a coroutine — mocks must use `return_value=` with a proper `__aenter__`/`__aexit__`, not `side_effect=` with an async function.
- Test framework: pytest + pytest-asyncio with `asyncio_mode = "auto"` (no need for `@pytest.mark.asyncio` decorators).
- Store tests use real SQLite via `tmp_path` — fast and deterministic, no in-memory hacks needed since aiosqlite file paths work fine with temp dirs.

### 2026-04-21 — Youth team filter tests added
- Added `TestIsYouthTeam` class (14 unit tests) covering U17-U23 suffixes, Sub-NN format, Youth/Primavera/Juvenil/Reserves keywords, case insensitivity, and false-positive guard for teams like Schalke 04.
- Added integration test `test_filters_youth_teams` in `TestRedditGoalScanner` verifying that posts with youth team names in titles are skipped even when the senior team is monitored.
- Tests written ahead of implementation (TDD-style, coordinating with Fry who is adding `_is_youth_team` and `_YOUTH_TEAM_RE` to `scanner.py`).
