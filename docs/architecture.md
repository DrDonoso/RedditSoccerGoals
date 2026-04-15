# SoccerGoals — Architecture

> A background process that detects soccer goals in live matches and retrieves matching video clips from Reddit.

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│                   SoccerGoals Worker                    │
│                                                         │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │  Match    │──▶│  Reddit      │──▶│  Media         │  │
│  │  Poller   │   │  Searcher    │   │  Downloader    │  │
│  └──────────┘   └──────────────┘   └────────────────┘  │
│       │                │                    │           │
│       ▼                ▼                    ▼           │
│  ┌─────────────────────────────────────────────────┐    │
│  │              State Store (SQLite)               │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
   ┌───────────┐                     ┌──────────────┐
   │ Football  │                     │  Local Disk / │
   │ API       │                     │  Object Store │
   └───────────┘                     └──────────────┘
```

## Components

### 1. Match Poller

**Responsibility:** Detect when a goal is scored in a live match.

**Approach:** Poll a football data API on a fixed interval (30–60 seconds) for live match events. Compare current score state against last-known state to detect new goals.

**Data source options (ranked):**

| Source | Free Tier | Real-time | Notes |
|--------|-----------|-----------|-------|
| [API-Football](https://www.api-football.com/) (via RapidAPI) | 100 req/day | ~1 min delay | Best balance of data quality and free access |
| [football-data.org](https://www.football-data.org/) | 10 req/min | ~2 min delay | Simpler API, fewer leagues |
| [SportMonks](https://www.sportmonks.com/) | Limited | ~1 min delay | Richer data, paid quickly |

**Recommendation:** Start with **API-Football** (free tier). It provides live fixture events including goal scorers, minute, and match context — everything we need for the title template.

**Interface:**

```python
@dataclass
class GoalEvent:
    match_id: str
    scorer: str
    assist: str | None
    minute: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    league: str
    timestamp: datetime

class MatchPoller(Protocol):
    async def poll_live_matches(self) -> list[GoalEvent]: ...
```

### 2. Reddit Searcher

**Responsibility:** Given a `GoalEvent`, build a search query from a title template and find matching Reddit posts (primarily from r/soccer).

**Approach:**
- Use a configurable **title template** to construct the Reddit search query. Example template: `{scorer} {home_score}-{away_score} {home_team} vs {away_team}`
- Search r/soccer (and optionally other subreddits) using Reddit's API
- Rank results by recency and relevance
- Filter for posts containing video/media links

**Rate limiting:** Reddit API allows 100 requests per minute per OAuth client. We'll stay well under this with natural polling cadence.

**Interface:**

```python
@dataclass
class RedditPost:
    post_id: str
    title: str
    url: str            # direct link or reddit post URL
    media_url: str | None  # extracted streamable/video URL
    score: int
    created_utc: datetime
    subreddit: str

class RedditSearcher(Protocol):
    async def search_goal_clip(
        self, event: GoalEvent, template: str
    ) -> list[RedditPost]: ...
```

### 3. Media Downloader

**Responsibility:** Download the video from the best matching Reddit post.

**Approach:**
- Reddit goal clips typically link to: Streamable, Streamin, Streamja, v.redd.it, or similar hosts
- Use **yt-dlp** as the extraction engine — it supports all major video hosts and handles format selection
- Download to a local directory with structured naming: `{date}/{league}/{home_team}_vs_{away_team}_{scorer}_{minute}.mp4`

**Interface:**

```python
@dataclass
class DownloadResult:
    event: GoalEvent
    file_path: Path
    source_url: str
    file_size_bytes: int
    duration_seconds: float | None

class MediaDownloader(Protocol):
    async def download(
        self, post: RedditPost, event: GoalEvent, output_dir: Path
    ) -> DownloadResult: ...
```

### 4. State Store

**Responsibility:** Track which goals have been processed to avoid duplicates and enable retry.

**Technology:** **SQLite** — zero-config, file-based, perfect for a single-process background worker.

**Tables:**

```sql
CREATE TABLE processed_goals (
    id            INTEGER PRIMARY KEY,
    match_id      TEXT NOT NULL,
    scorer        TEXT NOT NULL,
    minute        INTEGER NOT NULL,
    event_hash    TEXT UNIQUE NOT NULL,  -- dedup key
    status        TEXT NOT NULL,          -- pending | downloaded | failed | no_clip
    reddit_post_id TEXT,
    file_path     TEXT,
    error_message TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE poll_state (
    id            INTEGER PRIMARY KEY,
    last_poll_at  TIMESTAMP,
    fixtures_hash TEXT  -- detect fixture list changes
);
```

### 5. Orchestrator (Main Loop)

**Responsibility:** Wire the components together and run the polling loop.

```
every 45 seconds:
  1. Poll live matches → detect new GoalEvents
  2. For each new goal (not in state store):
     a. Search Reddit for matching clip
     b. If found → download media
     c. Record result in state store
  3. Retry any previously failed goals (up to 3 attempts, with backoff)
  4. Sleep until next interval
```

## Tech Stack

| Layer | Choice | Justification |
|-------|--------|---------------|
| **Language** | Python 3.12+ | Best ecosystem for this task: PRAW/asyncpraw (Reddit), yt-dlp (media), httpx (HTTP), rich CLI libraries. Quick to prototype and iterate. |
| **Async runtime** | asyncio + httpx | Non-blocking I/O for parallel API calls and downloads |
| **Reddit client** | asyncpraw | Official async Reddit API wrapper, handles OAuth and rate limiting |
| **Media extraction** | yt-dlp | Industry-standard video downloader, supports 1000+ sites |
| **Database** | SQLite (via aiosqlite) | Zero-config state persistence, perfect for single-process workers |
| **Config** | TOML (pyproject.toml + config.toml) | Python-native config format, clean and readable |
| **Scheduling** | Built-in asyncio loop | No need for Celery/APScheduler for a single polling loop |
| **Packaging** | uv | Fast, modern Python package manager |
| **Hosting** | Local machine / cheap VPS / Docker | Single process, low resource needs. Docker optional but straightforward. |

### Why Python over alternatives?

- **vs. C#/.NET:** Stronger library ecosystem for Reddit + video extraction. yt-dlp is Python-native. PRAW is battle-tested. .NET would require wrapping CLI tools or HTTP-only Reddit access.
- **vs. Node.js:** Python's yt-dlp and PRAW are more mature than JS equivalents. Data processing is more natural in Python.
- **vs. Go:** Same library gap. Go excels at infrastructure, not media/API glue work.

## Configuration

```toml
# config.toml
[polling]
interval_seconds = 45
leagues = ["Premier League", "La Liga", "Champions League"]  # filter

[football_api]
provider = "api-football"   # extensible to other providers
base_url = "https://v3.football.api-sports.io"
# API key stored in environment variable: FOOTBALL_API_KEY

[reddit]
subreddits = ["soccer"]
title_template = "{scorer} {home_score}-{away_score} {home_team} vs {away_team}"
max_results = 5
max_post_age_minutes = 30   # ignore old posts

[media]
output_dir = "./downloads"
max_file_size_mb = 100
preferred_format = "mp4"
max_retries = 3

[database]
path = "./data/soccergoals.db"
```

## Error Handling & Resilience

| Scenario | Strategy |
|----------|----------|
| Football API down | Log warning, skip poll cycle, retry next interval |
| Reddit API rate limited | Respect `Retry-After` header, exponential backoff |
| Reddit search returns no results | Mark goal as `no_clip`, retry up to 3 times with increasing delay (clips may be posted late) |
| yt-dlp download fails | Mark as `failed`, retry with backoff. Some hosts die quickly — accept this. |
| Duplicate goal detection | Hash `match_id + scorer + minute` for dedup |
| Process crash | SQLite state survives restart. On startup, retry `pending`/`failed` goals. |
| API key missing/invalid | Fail fast on startup with clear error message |

## Directory Structure (Proposed)

```
SoccerGoals/
├── src/
│   └── soccergoals/
│       ├── __init__.py
│       ├── main.py              # Entry point, orchestrator loop
│       ├── config.py            # Config loading and validation
│       ├── models.py            # GoalEvent, RedditPost, DownloadResult
│       ├── poller.py            # MatchPoller (football API integration)
│       ├── searcher.py          # RedditSearcher
│       ├── downloader.py        # MediaDownloader (yt-dlp wrapper)
│       └── store.py             # SQLite state store
├── tests/
│   ├── test_poller.py
│   ├── test_searcher.py
│   ├── test_downloader.py
│   └── test_store.py
├── config.toml                  # User configuration
├── pyproject.toml               # Project metadata + dependencies
├── README.md
└── docs/
    └── architecture.md          # This file
```

## Open Questions for @drdonoso

1. **Leagues scope:** Which leagues should we monitor? All live matches, or a configurable subset? The free API tier (100 req/day) limits how many concurrent fixtures we can track.

2. **Media storage:** Local disk is simplest. Do you need cloud storage (S3, etc.) or serving via a web endpoint? This affects complexity significantly.

3. **Notifications:** Should the system notify you when a goal clip is captured? (e.g., Discord webhook, desktop notification, Telegram bot). Easy to add but worth scoping now.

4. **Title template flexibility:** The r/soccer convention is fairly stable (`Player [Score] Team vs Team`), but do you want to support multiple subreddits with different naming conventions?

5. **Hosting preference:** Running locally on your machine? A Raspberry Pi? A cloud VPS? Docker container? This affects how we handle process supervision (systemd, Docker restart policies, etc.).

6. **Retention policy:** Keep all videos forever, or auto-prune after N days? Disk space consideration.

7. **Python version/tooling preference:** Any strong feelings on uv vs pip vs poetry? Python 3.12+ ok?
