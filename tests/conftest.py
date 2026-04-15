from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from soccergoals.models import DownloadResult, GoalEvent, RedditPost


# ── Environment / Config ────────────────────────────────────────────

FAKE_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-telegram-token",
    "TELEGRAM_CHANNEL_ID": "@test_channel",
    "MONITORED_TEAMS": "Real Madrid, Barcelona, Manchester City",
    "POLLING_INTERVAL_SECONDS": "10",
    "MAX_POST_AGE_MINUTES": "15",
    "MAX_RETRIES": "2",
}


@pytest.fixture()
def fake_env(tmp_path: Path):
    """Patch os.environ with all required config variables."""
    env = {
        **FAKE_ENV,
        "DB_PATH": str(tmp_path / "test.db"),
        "TEMP_DIR": str(tmp_path / "tmp"),
    }
    with patch.dict(os.environ, env, clear=False):
        yield env


@pytest.fixture()
def config(fake_env):
    """Return a Config instance backed by fake environment variables."""
    from soccergoals.config import Config

    return Config()


# ── Sample model instances ──────────────────────────────────────────

@pytest.fixture()
def sample_goal_event() -> GoalEvent:
    return GoalEvent(
        event_id="real_madrid_vs_barcelona_2026-04-15",
        scorer="Vinícius Júnior",
        minute=23,
        home_team="Real Madrid",
        away_team="Barcelona",
        home_score=1,
        away_score=0,
        timestamp=datetime(2026, 4, 15, 20, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def sample_reddit_post() -> RedditPost:
    return RedditPost(
        post_id="abc123",
        title="Real Madrid [1] - 0 Barcelona - Vinícius Júnior 23'",
        url="https://streamff.link/v/abc123",
        media_url="https://streamff.link/v/abc123",
        score=120,
        created_utc=datetime(2026, 4, 15, 20, 32, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def sample_download_result(sample_goal_event: GoalEvent, tmp_path: Path) -> DownloadResult:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 1024)
    return DownloadResult(
        event=sample_goal_event,
        file_path=video,
        source_url="https://streamff.link/v/abc123",
        file_size_bytes=1024,
        duration_seconds=None,
    )
