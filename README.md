# SoccerGoals

Automated goal clip delivery — scans r/soccer for goal posts involving your teams and sends video clips to your Telegram channel.

## How It Works

1. **Scan** — Browses r/soccer/new every ~30 seconds, parses goal post titles, filters for your monitored teams
2. **Download** — Grabs the video (streamff.link preferred, yt-dlp fallback)
3. **Send** — Delivers the clip to your Telegram channel with match context

Runs as a single Docker container. SQLite tracks state to avoid duplicates and enable retries.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- API keys for:
  - [Reddit API](https://www.reddit.com/prefs/apps) (OAuth app)
  - [Telegram Bot](https://t.me/BotFather) (bot token + channel)

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/drdonoso/SoccerGoals.git
cd SoccerGoals

# 2. Set your credentials (edit docker-compose.yml or use a .env file)
cp docker-compose.yml docker-compose.yml  # edit environment variables

# 3. Start the container
docker-compose up -d

# 4. Check logs
docker-compose logs -f
```

To stop: `docker-compose down`

## Configuration

All config is via environment variables in `docker-compose.yml`. You can also use a `.env` file in the same directory.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDDIT_CLIENT_ID` | Yes | — | Reddit OAuth app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | — | Reddit OAuth app secret |
| `REDDIT_USER_AGENT` | No | `SoccerGoals/1.0` | User-Agent for Reddit API |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token from BotFather |
| `TELEGRAM_CHANNEL_ID` | Yes | — | Telegram channel ID (e.g. `-100XXXXXXXXXX`) |
| `MONITORED_TEAMS` | No | `Espanyol,Real Madrid,Barcelona,Atletico Madrid` | Comma-separated team names |
| `POLLING_INTERVAL_SECONDS` | No | `30` | Seconds between scan cycles |
| `MAX_POST_AGE_MINUTES` | No | `30` | Max age of Reddit posts to consider |
| `MAX_RETRIES` | No | `3` | Retry attempts for failed operations |

## Getting API Keys

### Reddit

1. Go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
2. Click "create another app..." at the bottom
3. Select **script**, fill in a name and redirect URI (`http://localhost:8080`)
4. Note the **client ID** (under the app name) and **client secret**

### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow the prompts — you'll get a **bot token**
3. Create a channel, add your bot as an admin
4. To get the channel ID: forward a message from the channel to [@userinfobot](https://t.me/userinfobot), or use the Telegram API

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full system design, component interfaces, data models, and error handling strategy.

## Development

To run locally without Docker:

```bash
# Install dependencies (requires Python 3.12+ and uv)
uv sync

# Set environment variables
export REDDIT_CLIENT_ID="your-id"
export REDDIT_CLIENT_SECRET="your-secret"
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHANNEL_ID="your-channel-id"
export MONITORED_TEAMS="Espanyol,Real Madrid"

# Run
python -m soccergoals
```

You'll also need `ffmpeg` and `yt-dlp` installed locally.
