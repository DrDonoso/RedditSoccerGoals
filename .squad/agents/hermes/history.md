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
