# Nibbler — History

## Project Context
- **Project:** SoccerGoals — Background process that monitors r/soccer for goal posts, downloads video clips, sends to Telegram
- **Owner:** drdonoso
- **Stack:** Python 3.12+, asyncio, httpx, aiosqlite, python-telegram-bot, yt-dlp, Docker
- **Sensitive items:** Telegram bot token, Telegram channel ID (stored in .env, excluded from git)
- **Key files:** .env, .gitignore, .dockerignore, Dockerfile, docker-compose.yml, .github/workflows/

## Learnings

### 2026-04-15 — Initial Security Audit
- **__pycache__ committed to git**: 19 .pyc files were tracked in git (src/ and tests/). .gitignore had no `__pycache__/` or `*.pyc` entries. Fixed by adding entries and running `git rm -r --cached`. These contain bytecode that can leak source paths and shouldn't be in repos.
- **.gitignore missing `.env.*` glob**: Only `.env` was excluded. Added `.env.*` with `!.env.example` exception to cover `.env.local`, `.env.production`, etc.
- **No `.env.example` existed**: Developers had no safe reference for required env vars. Created `.env.example` with placeholder values.
- **Dockerfile is well-secured**: Uses `python:3.12-slim`, creates non-root user (`app:1000`), uses `USER app`, no `ADD` from URLs, no secrets baked in. Good.
- **docker-compose.yml uses `${VAR}` interpolation**: All secrets reference env vars, no hardcoded values. Good.
- **.dockerignore is comprehensive**: Excludes `.env`, `.git`, `tests/`, `data/`, etc. Good.
- **GitHub Actions uses `secrets.*`**: Docker credentials use `secrets.DOCKER_USERNAME` and `secrets.DOCKER_PASSWORD`. Good.
- **No hardcoded secrets in source**: All credentials loaded from env vars via `config.py`. Test files use fake values (`test-telegram-token`). Good.
- **No .env ever committed to git history**: Verified via `git log --diff-filter=A`.
- **Git history clean of live secrets**: Searched for token/password/secret patterns. Found only removed `REDDIT_CLIENT_SECRET` references from the pre-refactor era — these were config schema references, not actual values.
- **Subprocess calls are safe**: `create_subprocess_exec` (not shell=True), yt-dlp uses `--` separator before URL arg, ffprobe uses hardcoded args with internal file paths.
- **SQL uses parameterized queries**: All `store.py` queries use `?` placeholders. No string interpolation in SQL. Good.
- **Dependencies not pinned to exact versions**: Uses `>=` lower bounds only (e.g. `httpx>=0.27`). Low risk for a personal project but noted.
- **No SSRF risk**: Reddit URL is hardcoded (`REDDIT_NEW_URL`). Media URLs come from Reddit but are downloaded server-side to send to Telegram — this is by design and the URLs are validated by regex patterns.
