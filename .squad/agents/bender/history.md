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

### 2026-04-21 — Docker image versioning analysis
- **Current state:** `docker-deploy.yml` triggers on push to `main`, pushes `:latest` + `:${{ github.run_number }}` tags. Run number has no semantic meaning.
- **Recommendation:** Git tag-triggered semver with `docker/metadata-action`. Dual trigger: `main` push → `:latest` only; `v*` tag push → `:X.Y.Z` + `:latest`.
- **Key files:** `.github/workflows/docker-deploy.yml`, `pyproject.toml` (version field for reference, not as build source of truth)
- **Decision status:** Proposed, awaiting drdonoso confirmation. See `.squad/decisions/inbox/bender-versioning-recommendation.md`.

### 2026-04-21 — CalVer versioning in CI
- **Change:** Replaced `github.run_number` tags with CalVer (`yyyymmdd`) in `docker-deploy.yml`.
- **Format:** First push of the day = `20260421`, subsequent = `20260421.02`, `.03`, etc. No `.01` suffix.
- **Implementation:** `date -u +%Y%m%d` + `git tag -l` to find existing tags, suffix incremented with zero-padded `printf "%02d"`.
- **GitHub Release:** Added `gh release create` with `--generate-notes` for auto-changelog. Required `permissions: contents: write` on the job.
- **Checkout:** Added `fetch-depth: 0` so all tags are available for the CalVer computation.
- **Docker tags:** `:latest` + `:{calver}` pushed to DockerHub.
