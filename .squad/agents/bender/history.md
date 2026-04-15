# Bender — History

## Project Context
- **Project:** SoccerGoals — Background process that monitors r/soccer for goal posts, downloads video clips, sends to Telegram
- **Owner:** drdonoso
- **Stack:** Python 3.12+, asyncio, httpx, aiosqlite, python-telegram-bot, yt-dlp, Docker
- **Key files:** Dockerfile, docker-compose.yml, pyproject.toml, src/soccergoals/

## Learnings

### 2026-04-15 — Docker build fix (dependency caching bug)
- **Problem:** `pip install --no-cache-dir .` ran before `COPY src/` — setuptools couldn't discover packages (`[tool.setuptools.packages.find] where = ["src"]`), so the build failed.
- **Fix:** Two-stage install pattern: (1) copy `pyproject.toml`, create a stub `src/soccergoals/__init__.py`, and `pip install .` to cache dependencies; (2) `COPY src/` with full source and `pip install --no-deps .` to reinstall only the package itself.
- **Result:** Build succeeds. Dependencies are cached unless `pyproject.toml` changes. Source-only changes only re-run the fast `--no-deps` install step.
- **Image size:** ~610 MB (python:3.12-slim + ffmpeg + yt-dlp + app deps).
