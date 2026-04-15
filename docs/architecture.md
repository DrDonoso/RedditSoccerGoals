# SoccerGoals — Architecture

> A background process that monitors r/soccer for goal posts involving configured teams and sends matching video clips to a Telegram channel.

## System Overview

```
                       ┌──────────────────────────────────────────────────────┐
                       │                  SoccerGoals Worker                  │
                       │                                                      │
                       │  ┌───────────────┐  ┌──────────────┐  ┌───────────┐ │
                       │  │ Reddit Goal   │─▶│    Media     │─▶│ Telegram  │ │
                       │  │ Scanner       │  │  Downloader  │  │  Sender   │ │
                       │  └───────────────┘  └──────────────┘  └───────────┘ │
                       │         │                  │                │        │
                       │         ▼                  ▼                ▼        │
                       │  ┌──────────────────────────────────────────────┐    │
                       │  │            State Store (SQLite)              │    │
                       │  └──────────────────────────────────────────────┘    │
                       └──────────────────────────────────────────────────────┘
                                │                                    │
                                ▼                                    ▼
                         ┌────────────┐                     ┌──────────────┐
                         │   Reddit   │                     │   Telegram   │
                         │ (r/soccer) │                     │   Channel    │
                         └────────────┘                     └──────────────┘
```

Single external data dependency: **Reddit** (r/soccer). No football API.

## Components

### 1. Reddit Goal Scanner

**Responsibility:** Browse r/soccer/new, detect goal posts via regex, filter for monitored teams, and extract media URLs. This component replaces both the old Match Poller and Reddit Searcher — there is no football API dependency.

**Source:** Always **r/soccer/new** — hardcoded, not configurable.

**Approach:** Poll `r/soccer/new` via asyncpraw every ~30 seconds, fetching the newest ~50 posts. For each post, attempt to parse the title as a goal post using a regex pattern. If the title matches and involves a monitored team, extract the media URL and emit a `GoalEvent` + `RedditPost` pair.

**Title convention (actual r/soccer format):**

r/soccer goal post titles follow two known variants — brackets around the new score are **common but not guaranteed**:

```
# Variant A — with brackets (most common)
{home_team} [{home_score}] - {away_score} {away_team} [{aggregate}] - {scorer} {minute}'

# Variant B — without brackets
{home_team} {home_score} - {away_score} {away_team} - {scorer} {minute}'
```

Examples:
- **With brackets:** `Atletico Madrid [1] - 2 Barcelona [3-2 on agg.] - Ademola Lookman 31'`
- **Without brackets:** `Atletico Madrid 1 - 0 Barcelona - Ademola Lookman 31'`

**Goal title regex:**

```python
GOAL_TITLE_PATTERN = re.compile(
    r"^(?P<home_team>.+?)\s+"           # Home team name (non-greedy)
    r"\[?(?P<home_score>\d+)\]?\s*"     # Home score, optional brackets
    r"-\s*"                              # Score separator
    r"\[?(?P<away_score>\d+)\]?\s+"     # Away score, optional brackets
    r"(?P<away_team>.+?)\s+"            # Away team name (non-greedy)
    r"(?:\[.*?\]\s*)?"                   # Optional aggregate in brackets (ignored)
    r"-\s+"                              # Separator before scorer
    r"(?P<scorer>.+?)\s+"               # Scorer name (non-greedy)
    r"(?P<minute>\d+)['+]",              # Minute with trailing ' or +
    re.IGNORECASE,
)
```

This pattern handles both bracket and non-bracket variants, optional aggregate info, and minute notation.

**Monitored team filtering:**

Posts are filtered against the configured `MONITORED_TEAMS` list. Matching uses:
- Case-insensitive comparison
- Fuzzy matching via an **alias map** — common alternate names for teams (e.g. `Atleti` → `Atletico Madrid`, `Barça` → `Barcelona`, `Real` → `Real Madrid`)
- A match is relevant if **either** the home or away team matches a monitored team or one of its aliases

**Media URL extraction:**
- The scanner checks both `post.url` and `post.selftext` for a **streamff.link** URL
- streamff.link is the primary (and often only) video host for r/soccer goal clips
- If no streamff.link URL is found, the post is skipped as `no_clip`

**Interface:**

```python
@dataclass
class GoalEvent:
    event_id: str             # derived: normalized team names + date (e.g. "atletico_madrid_vs_barcelona_2026-04-15")
    scorer: str
    minute: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    timestamp: datetime

@dataclass
class RedditPost:
    post_id: str
    title: str
    url: str                  # direct link or reddit post URL
    media_url: str | None     # extracted streamff.link URL
    score: int
    created_utc: datetime

@dataclass
class ScanResult:
    event: GoalEvent
    post: RedditPost

class RedditGoalScanner(Protocol):
    async def scan_new_posts(
        self, monitored_teams: list[str]
    ) -> list[ScanResult]: ...
```

### 2. Media Downloader

**Responsibility:** Download the video from the matched Reddit post.

**Approach:**
- **Primary target: streamff.link** — the dominant video host for r/soccer goal clips. The downloader fetches the streamff page, extracts the direct video URL, and downloads it via httpx.
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

### 3. Telegram Sender

**Responsibility:** Send the downloaded goal clip video to a configured Telegram channel, along with context (scorer, teams, score, minute).

**Approach:**
- Use **python-telegram-bot** library to interact with the Telegram Bot API
- Send the video file with a caption containing match details
- Caption format: `⚽ {scorer} {minute}' — {home_team} {home_score}-{away_score} {away_team}`
- Requires a bot token and target channel ID (configured via environment variables)

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

### 4. State Store

**Responsibility:** Track which goals have been processed to avoid duplicates and enable retry.

**Technology:** **SQLite** — zero-config, file-based, perfect for a single-process background worker.

**Tables:**

```sql
CREATE TABLE processed_goals (
    id              INTEGER PRIMARY KEY,
    event_id        TEXT NOT NULL,
    event_hash      TEXT UNIQUE NOT NULL,   -- dedup key (see below)
    scorer          TEXT NOT NULL,
    minute          INTEGER NOT NULL,
    status          TEXT NOT NULL,           -- pending | downloaded | sent | failed | send_failed | no_clip
    reddit_post_id  TEXT,
    file_path       TEXT,
    telegram_msg_id INTEGER,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE seen_posts (
    post_id     TEXT PRIMARY KEY,            -- Reddit post ID (layer 1 dedup)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Deduplication Strategy

Two-layer dedup prevents reprocessing goals:

**Layer 1 — Post ID (fast skip):**
Every Reddit post has a unique `post_id`. Before parsing a title, check if `post_id` is already in the `seen_posts` table. If so, skip immediately. This avoids re-parsing the same post on every scan cycle.

**Layer 2 — Event Hash (semantic dedup):**
Multiple Reddit posts may cover the same goal (e.g., different users posting, or a corrected title). To catch these, compute a deterministic hash from the parsed goal data:

```python
event_hash = sha256(
    normalize(home_team) + "|" +
    normalize(away_team) + "|" +
    normalize(scorer_surname) + "|" +
    str(minute)
).hexdigest()[:16]
```

**Normalization rules:**
- **Lowercase** — `"Atletico Madrid"` → `"atletico madrid"`
- **Strip accents/diacritics** — `"Müller"` → `"muller"`, `"Barça"` → `"barca"` (via `unicodedata.normalize("NFKD")` + strip combining marks)
- **Extract last name** — for the scorer, use only the surname: `"Ademola Lookman"` → `"lookman"`

If `event_hash` already exists in `processed_goals`, the goal is a duplicate and is skipped.

### 5. Orchestrator (Main Loop)

**Responsibility:** Wire the components together and run the polling loop.

```
every 30 seconds:
  1. Fetch newest ~50 posts from r/soccer/new
  2. For each post:
     a. Skip if post_id already in state store (layer 1 dedup)
     b. Try to parse title as goal post (regex)
     c. If not a goal → skip
     d. If no monitored team → skip
     e. Compute event_hash → skip if already processed (layer 2 dedup)
     f. Extract media URL (streamff.link primary)
     g. If no media → record as no_clip, skip
     h. Download media
     i. Send to Telegram
     j. Record in state store
  3. Retry previously failed goals (up to 3 attempts, with backoff)
  4. Clean up temporary downloaded files
  5. Sleep until next interval
```

## Tech Stack

| Layer | Choice | Justification |
|-------|--------|---------------|
| **Language** | Python 3.12+ | Best ecosystem for this task: asyncpraw (Reddit), yt-dlp (media), httpx (HTTP). Quick to prototype and iterate. |
| **Async runtime** | asyncio + httpx | Non-blocking I/O for concurrent downloads and API calls |
| **Reddit client** | asyncpraw | Official async Reddit API wrapper, handles OAuth and rate limiting |
| **Media extraction** | yt-dlp | Fallback video downloader for non-streamff hosts, supports 1000+ sites |
| **Media download** | httpx | Direct HTTP download for streamff.link videos |
| **Telegram** | python-telegram-bot | Async Telegram Bot API client for sending videos to channels |
| **Database** | SQLite (via aiosqlite) | Zero-config state persistence, perfect for single-process workers |
| **Config** | Environment variables (docker-compose.yml) | Twelve-factor app style, no config files to manage |
| **Scheduling** | Built-in asyncio loop | No need for Celery/APScheduler for a single polling loop |
| **Packaging** | uv | Fast, modern Python package manager |
| **Hosting** | Docker (docker-compose) | Single container, restart policy, env-var-driven config |

### Why Python over alternatives?

- **vs. C#/.NET:** Stronger library ecosystem for Reddit + video extraction. yt-dlp is Python-native. PRAW is battle-tested. .NET would require wrapping CLI tools or HTTP-only Reddit access.
- **vs. Node.js:** Python's yt-dlp and PRAW are more mature than JS equivalents. Data processing is more natural in Python.
- **vs. Go:** Same library gap. Go excels at infrastructure, not media/API glue work.

## Configuration

All configuration is driven by **environment variables** defined in `docker-compose.yml`. The application reads from `os.environ` at startup — no config files needed.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDDIT_CLIENT_ID` | Yes | — | Reddit OAuth app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | — | Reddit OAuth app client secret |
| `REDDIT_USER_AGENT` | No | `SoccerGoals/1.0` | User-Agent string for Reddit API requests |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token from BotFather |
| `TELEGRAM_CHANNEL_ID` | Yes | — | Target Telegram channel ID (e.g. `-100XXXXXXXXXX`) |
| `MONITORED_TEAMS` | Yes | — | Comma-separated team names (e.g. `Espanyol,Real Madrid,Barcelona`) |
| `POLLING_INTERVAL_SECONDS` | No | `30` | Seconds between each scan cycle |
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
│       ├── __main__.py       # Package entry point (python -m soccergoals)
│       ├── main.py           # Orchestrator loop
│       ├── config.py         # Config loading and validation
│       ├── models.py         # GoalEvent, RedditPost, ScanResult, DownloadResult, SendResult
│       ├── scanner.py        # Reddit Goal Scanner (r/soccer/new browsing + regex parsing)
│       ├── downloader.py     # Media Downloader (streamff + yt-dlp)
│       ├── sender.py         # Telegram Sender
│       └── store.py          # State Store (SQLite)
├── tests/
│   ├── conftest.py
│   ├── test_scanner.py
│   ├── test_downloader.py
│   ├── test_sender.py
│   ├── test_store.py
│   ├── test_models.py
│   └── test_config.py
└── docs/
    └── architecture.md       # This file
```

## Error Handling & Resilience

| Scenario | Strategy |
|----------|----------|
| Reddit /new fetch fails | Log warning, skip scan cycle, retry next interval. Respect `Retry-After` if rate-limited. |
| Reddit API rate limited | Respect `Retry-After` header, exponential backoff |
| No goal posts found in scan | Normal — not every scan cycle will find goals. No action needed. |
| Title regex doesn't match | Skip post silently — not a goal post (or unexpected format). |
| streamff.link download fails | Fall back to yt-dlp. If both fail, mark as `failed`, retry with backoff. |
| yt-dlp download fails | Mark as `failed`, retry with backoff. Some hosts die quickly — accept this. |
| Telegram send fails | Retry up to 3 times with backoff. If persistent, mark as `send_failed` in state store (video downloaded but not delivered). |
| Telegram rate limited | Respect Telegram rate limits (~30 msgs/sec for bots). Queue and retry. |
| Duplicate goal detection | Two-layer dedup: post_id (layer 1) + event_hash (layer 2). See Deduplication Strategy. |
| Process crash | SQLite state survives restart. On startup, retry `pending`/`failed` goals. |
| Container crash | `restart: unless-stopped` policy in docker-compose ensures automatic recovery. SQLite data persists via volume mount. |
| Env var missing/invalid | Fail fast on startup with clear error message. |

## Open Questions for @drdonoso

1. **Retention policy:** Downloaded videos are temporary (deleted after Telegram send). Should we keep any local archive, or is Telegram the sole record?

2. **Team name aliases in config:** Should users be able to define custom aliases (e.g. `Barça=Barcelona`) in the env vars or in a separate mapping, or is the built-in alias map sufficient?

3. **Python version/tooling preference:** Any strong feelings on uv vs pip vs poetry? Python 3.12+ ok?
