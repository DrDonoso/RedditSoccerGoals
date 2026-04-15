# SoccerGoals — Architecture

> A background process that detects soccer goals in live matches and retrieves matching video clips from Reddit.

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      SoccerGoals Worker                         │
│                                                                  │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │  Match    │─▶│  Reddit      │─▶│  Media       │─▶│Telegram │ │
│  │  Poller   │  │  Searcher    │  │  Downloader  │  │ Sender  │ │
│  └──────────┘  └──────────────┘  └──────────────┘  └─────────┘ │
│       │               │                │                │       │
│       ▼               ▼                ▼                ▼       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                 State Store (SQLite)                     │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
         │                                          │
         ▼                                          ▼
   ┌───────────┐                           ┌──────────────┐
   │ Football  │                           │   Telegram   │
   │ API       │                           │   Channel    │
   └───────────┘                           └──────────────┘
```

## Components

### 1. Match Poller

**Responsibility:** Detect when a goal is scored in a live match.

**Approach:** Poll a football data API on a fixed interval (30–60 seconds) for live match events. Compare current score state against last-known state to detect new goals. Only goals involving **configured monitored teams** are processed — filtering is team-based, not league-based.

**Data source options (ranked):**

| Source | Free Tier | Real-time | Notes |
|--------|-----------|-----------|-------|
| [API-Football](https://www.api-football.com/) (via RapidAPI) | 100 req/day | ~1 min delay | Best balance of data quality and free access |
| [football-data.org](https://www.football-data.org/) | 10 req/min | ~2 min delay | Simpler API, fewer leagues |
| [SportMonks](https://www.sportmonks.com/) | Limited | ~1 min delay | Richer data, paid quickly |

**Recommendation:** Start with **API-Football** (free tier). It provides live fixture events including goal scorers, minute, and match context — everything we need for the title template.

**Filtering:** The poller retrieves all live fixtures but only emits `GoalEvent`s for matches where **at least one** of the configured `monitored` teams is playing (home or away). This keeps API usage efficient while supporting any league/competition.

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
    scoring_team: str        # which team scored (authoritative, from football API — not derived from Reddit title brackets)
    aggregate: str | None    # e.g. "3-2 on agg." — optional
    timestamp: datetime

class MatchPoller(Protocol):
    async def poll_live_matches(
        self, monitored_teams: list[str]
    ) -> list[GoalEvent]: ...
```

### 2. Reddit Searcher

**Responsibility:** Given a `GoalEvent`, build a search query from the r/soccer title convention and find matching Reddit posts. Extract the **streamff.link** video URL from the post.

**Source:** Always **r/soccer** — hardcoded, not configurable.

**Title convention (actual r/soccer format):**

r/soccer titles follow two known variants — brackets around the new score are **common but not guaranteed**:

```
# Variant A — with brackets (most common)
{home_team} [{home_score}] - {away_score} {away_team} [{aggregate}] - {scorer} {minute}'

# Variant B — without brackets
{home_team} {home_score} - {away_score} {away_team} - {scorer} {minute}'
```

Examples:
- **With brackets:** `Atletico Madrid [1] - 2 Barcelona [3-2 on agg.] - Ademola Lookman 31'`
- **Without brackets:** `Atletico Madrid 1 - 0 Barcelona - Ademola Lookman 31'`

- When present, `[X]` next to a team name indicates **who scored** (the team with brackets around the new score)
- When brackets are absent, the title alone does **not** indicate which team scored
- Aggregate info like `[3-2 on agg.]` is optional and only appears in the bracketed variant
- The `'` after the minute is standard

**Scoring team detection:** We do **not** rely on Reddit title brackets to determine which team scored. The `GoalEvent.scoring_team` field — sourced from the football API — is the authoritative source for goal attribution. Brackets in the title are useful only as an extra signal for search matching, never as a source of truth.

**Approach — search query strategy:**
- Build a **keyword-based** search query from the GoalEvent fields: scorer name, both team names, and current score digits. Do **not** attempt to match the exact bracket format, since it varies between posts.
- Example query for the goal above: `Atletico Madrid Barcelona Lookman 1 - 0` or `Atletico Madrid Barcelona Ademola Lookman`
- Search r/soccer using Reddit's API
- Rank results by recency and relevance
- Extract the **streamff.link** video URL from the post body/URL (this is the primary media host for r/soccer goal clips)
- Filter for posts containing video/media links
- The title parser should accept **both** bracket and non-bracket formats gracefully when validating search results

**Rate limiting:** Reddit API allows 100 requests per minute per OAuth client. We'll stay well under this with natural polling cadence.

**Interface:**

```python
@dataclass
class RedditPost:
    post_id: str
    title: str
    url: str               # direct link or reddit post URL
    media_url: str | None  # extracted streamff.link URL (primary) or other video host
    score: int
    created_utc: datetime

class RedditSearcher(Protocol):
    async def search_goal_clip(
        self, event: GoalEvent
    ) -> list[RedditPost]: ...
```

### 3. Media Downloader

**Responsibility:** Download the video from the best matching Reddit post.

**Approach:**
- **Primary target: streamff.link** — the dominant video host for r/soccer goal clips. The downloader should first attempt to extract and download from the streamff.link URL found in the Reddit post.
- **Fallback: yt-dlp** — for any other video hosts (Streamable, v.redd.it, Streamin, etc.), use yt-dlp as the extraction engine.
- Download to a temporary file for subsequent delivery to Telegram.

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
        self, post: RedditPost, event: GoalEvent
    ) -> DownloadResult: ...
```

### 4. Telegram Sender

**Responsibility:** Send the downloaded goal clip video to a configured Telegram channel, along with context (scorer, teams, score, minute).

**Approach:**
- Use **python-telegram-bot** library to interact with the Telegram Bot API
- Send the video file with a caption containing match details
- Caption format: `⚽ {scorer} {minute}' — {home_team} {home_score}-{away_score} {away_team}`
- Requires a bot token and target channel ID (configured in config.toml)

**Interface:**

```python
@dataclass
class SendResult:
    event: GoalEvent
    message_id: int
    channel_id: str
    success: bool
    error: str | None = None

class TelegramSender(Protocol):
    async def send_goal_clip(
        self, download: DownloadResult
    ) -> SendResult: ...
```

### 5. State Store

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
    status        TEXT NOT NULL,          -- pending | downloaded | sent | failed | send_failed | no_clip
    reddit_post_id TEXT,
    file_path     TEXT,
    telegram_msg_id INTEGER,
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

### 6. Orchestrator (Main Loop)

**Responsibility:** Wire the components together and run the polling loop.

```
every 45 seconds:
  1. Poll live matches (filtered to monitored teams) → detect new GoalEvents
  2. For each new goal (not in state store):
     a. Search r/soccer for matching clip
     b. If found → download media (streamff.link first, yt-dlp fallback)
     c. Send video to Telegram channel with match context
     d. Record result in state store
  3. Retry any previously failed goals (up to 3 attempts, with backoff)
  4. Clean up temporary downloaded files
  5. Sleep until next interval
```

## Tech Stack

| Layer | Choice | Justification |
|-------|--------|---------------|
| **Language** | Python 3.12+ | Best ecosystem for this task: PRAW/asyncpraw (Reddit), yt-dlp (media), httpx (HTTP), rich CLI libraries. Quick to prototype and iterate. |
| **Async runtime** | asyncio + httpx | Non-blocking I/O for parallel API calls and downloads |
| **Reddit client** | asyncpraw | Official async Reddit API wrapper, handles OAuth and rate limiting |
| **Media extraction** | yt-dlp | Fallback video downloader for non-streamff hosts, supports 1000+ sites |
| **Media extraction (primary)** | httpx (direct download) | Primary downloader for streamff.link videos |
| **Telegram** | python-telegram-bot | Async Telegram Bot API client for sending videos to channels |
| **Database** | SQLite (via aiosqlite) | Zero-config state persistence, perfect for single-process workers |
| **Config** | Environment variables (docker-compose.yml) | Twelve-factor app style, no config files to manage |
| **Scheduling** | Built-in asyncio loop | No need for Celery/APScheduler for a single polling loop |
| **Packaging** | uv | Fast, modern Python package manager |
| **Hosting** | Docker (docker-compose) | Single container, restart policy, env-var-driven config |
| **Containerization** | Docker + docker-compose | Reproducible deployments, volume-mounted SQLite for persistence |

### Why Python over alternatives?

- **vs. C#/.NET:** Stronger library ecosystem for Reddit + video extraction. yt-dlp is Python-native. PRAW is battle-tested. .NET would require wrapping CLI tools or HTTP-only Reddit access.
- **vs. Node.js:** Python's yt-dlp and PRAW are more mature than JS equivalents. Data processing is more natural in Python.
- **vs. Go:** Same library gap. Go excels at infrastructure, not media/API glue work.

## Configuration

All configuration is driven by **environment variables** defined in `docker-compose.yml`. The application reads from `os.environ` at startup — no config files needed.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FOOTBALL_API_KEY` | Yes | — | API-Football key (via RapidAPI) |
| `REDDIT_CLIENT_ID` | Yes | — | Reddit OAuth app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | — | Reddit OAuth app client secret |
| `REDDIT_USER_AGENT` | No | `SoccerGoals/1.0` | User-Agent string for Reddit API requests |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token from BotFather |
| `TELEGRAM_CHANNEL_ID` | Yes | — | Target Telegram channel ID (e.g. `-100XXXXXXXXXX`) |
| `MONITORED_TEAMS` | Yes | — | Comma-separated team names (e.g. `Espanyol,Real Madrid,Barcelona`) |
| `POLLING_INTERVAL_SECONDS` | No | `45` | Seconds between each poll cycle |
| `MAX_POST_AGE_MINUTES` | No | `30` | Ignore Reddit posts older than this |
| `MAX_RETRIES` | No | `3` | Max retry attempts for failed operations |

The app reads these at startup and fails fast if any required variable is missing.

## Directory Structure

```
SoccerGoals/
├── Dockerfile                # Container image definition
├── docker-compose.yml        # Service config + environment variables
├── .dockerignore             # Files excluded from Docker build
├── pyproject.toml            # Python project metadata + dependencies
├── README.md                 # Quick start and usage guide
├── data/                     # SQLite database (volume-mounted, persisted)
│   └── soccergoals.db
├── src/
│   └── soccergoals/
│       ├── __init__.py
│       ├── main.py           # Entry point + orchestrator loop
│       ├── poller.py         # Match Poller (API-Football)
│       ├── searcher.py       # Reddit Searcher (r/soccer)
│       ├── downloader.py     # Media Downloader (streamff + yt-dlp)
│       ├── sender.py         # Telegram Sender
│       ├── store.py          # State Store (SQLite)
│       └── models.py         # Shared data models
├── tests/
└── docs/
    └── architecture.md       # This file
```

## Error Handling & Resilience

| Scenario | Strategy |
|----------|----------|
| Football API down | Log warning, skip poll cycle, retry next interval |
| Reddit API rate limited | Respect `Retry-After` header, exponential backoff |
| Reddit search returns no results | Mark goal as `no_clip`, retry up to 3 times with increasing delay (clips may be posted late) |
| streamff.link download fails | Fall back to yt-dlp. If both fail, mark as `failed`, retry with backoff. |
| yt-dlp download fails | Mark as `failed`, retry with backoff. Some hosts die quickly — accept this. |
| Telegram send fails | Retry up to 3 times with backoff. If persistent, mark as `send_failed` in state store (video downloaded but not delivered). |
| Telegram rate limited | Respect Telegram rate limits (~30 msgs/sec for bots). Queue and retry. |
| Duplicate goal detection | Hash `match_id + scorer + minute` for dedup |
| Process crash | SQLite state survives restart. On startup, retry `pending`/`failed` goals. |
| Container crash | `restart: unless-stopped` policy in docker-compose ensures automatic recovery. SQLite data persists via volume mount. |
| API key missing/invalid | Fail fast on startup with clear error message (includes Telegram bot token) |

## Directory Structure (Proposed)

```
SoccerGoals/
├── src/
│   └── soccergoals/
│       ├── __init__.py
│       ├── main.py              # Entry point, orchestrator loop
│       ├── config.py            # Config loading and validation
│       ├── models.py            # GoalEvent, RedditPost, DownloadResult, SendResult
│       ├── poller.py            # MatchPoller (football API, team-based filtering)
│       ├── searcher.py          # RedditSearcher (r/soccer, streamff.link extraction)
│       ├── downloader.py        # MediaDownloader (streamff.link primary, yt-dlp fallback)
│       ├── sender.py            # TelegramSender (python-telegram-bot)
│       └── store.py             # SQLite state store
├── tests/
│   ├── test_poller.py
│   ├── test_searcher.py
│   ├── test_downloader.py
│   ├── test_sender.py
│   └── test_store.py
├── config.toml                  # User configuration
├── pyproject.toml               # Project metadata + dependencies
├── README.md
└── docs/
    └── architecture.md          # This file
```

## Open Questions for @drdonoso

1. **Hosting preference:** Running locally on your machine? A Raspberry Pi? A cloud VPS? Docker container? This affects how we handle process supervision (systemd, Docker restart policies, etc.).

2. **Retention policy:** Downloaded videos are temporary (deleted after Telegram send). Should we keep any local archive, or is Telegram the sole record?

3. **Python version/tooling preference:** Any strong feelings on uv vs pip vs poetry? Python 3.12+ ok?
