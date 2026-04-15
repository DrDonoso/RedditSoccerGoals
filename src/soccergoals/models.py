from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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
    scoring_team: str
    aggregate: str | None
    timestamp: datetime


@dataclass
class RedditPost:
    post_id: str
    title: str
    url: str
    media_url: str | None
    score: int
    created_utc: datetime


@dataclass
class DownloadResult:
    event: GoalEvent
    file_path: Path
    source_url: str
    file_size_bytes: int
    duration_seconds: float | None


@dataclass
class SendResult:
    event: GoalEvent
    message_id: int
    channel_id: str
    success: bool
    error: str | None = None
