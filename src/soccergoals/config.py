from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def _require(name: str) -> str:
    """Return the value of a required environment variable or exit."""
    value = os.environ.get(name)
    if not value:
        logger.critical("Required environment variable %s is not set", name)
        sys.exit(1)
    return value


class Config:
    """Application configuration loaded entirely from environment variables."""

    def __init__(self) -> None:
        # Required
        self.football_api_key: str = _require("FOOTBALL_API_KEY")
        self.reddit_client_id: str = _require("REDDIT_CLIENT_ID")
        self.reddit_client_secret: str = _require("REDDIT_CLIENT_SECRET")
        self.telegram_bot_token: str = _require("TELEGRAM_BOT_TOKEN")
        self.telegram_channel_id: str = _require("TELEGRAM_CHANNEL_ID")

        teams_raw = _require("MONITORED_TEAMS")
        self.monitored_teams: list[str] = [
            t.strip() for t in teams_raw.split(",") if t.strip()
        ]

        # Optional with defaults
        self.reddit_user_agent: str = os.environ.get(
            "REDDIT_USER_AGENT", "SoccerGoals/1.0"
        )
        self.polling_interval: int = int(
            os.environ.get("POLLING_INTERVAL_SECONDS", "45")
        )
        self.max_post_age_minutes: int = int(
            os.environ.get("MAX_POST_AGE_MINUTES", "30")
        )
        self.max_retries: int = int(os.environ.get("MAX_RETRIES", "3"))
        self.db_path: str = os.environ.get("DB_PATH", "./data/soccergoals.db")
        self.temp_dir: str = os.environ.get("TEMP_DIR", "./tmp")
