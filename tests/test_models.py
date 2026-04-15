from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from soccergoals.models import DownloadResult, GoalEvent, RedditPost, SendResult


class TestGoalEvent:
    def test_create_with_all_fields(self):
        e = GoalEvent(
            match_id="1",
            scorer="Messi",
            assist="Di María",
            minute=45,
            home_team="Argentina",
            away_team="France",
            home_score=2,
            away_score=1,
            scoring_team="Argentina",
            aggregate="3-2",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert e.scorer == "Messi"
        assert e.assist == "Di María"
        assert e.aggregate == "3-2"

    def test_create_with_none_optionals(self):
        e = GoalEvent(
            match_id="2",
            scorer="Mbappé",
            assist=None,
            minute=90,
            home_team="A",
            away_team="B",
            home_score=0,
            away_score=1,
            scoring_team="B",
            aggregate=None,
            timestamp=datetime.now(timezone.utc),
        )
        assert e.assist is None
        assert e.aggregate is None

    def test_minute_zero(self):
        """Minute 0 is valid for kick-off goals."""
        e = GoalEvent(
            match_id="3",
            scorer="X",
            assist=None,
            minute=0,
            home_team="A",
            away_team="B",
            home_score=1,
            away_score=0,
            scoring_team="A",
            aggregate=None,
            timestamp=datetime.now(timezone.utc),
        )
        assert e.minute == 0


class TestRedditPost:
    def test_create_reddit_post(self):
        p = RedditPost(
            post_id="xyz",
            title="Goal title",
            url="https://example.com",
            media_url="https://streamff.link/v/xyz",
            score=50,
            created_utc=datetime.now(timezone.utc),
        )
        assert p.post_id == "xyz"
        assert p.media_url == "https://streamff.link/v/xyz"

    def test_media_url_none(self):
        p = RedditPost(
            post_id="abc",
            title="No media",
            url="https://reddit.com/r/soccer/...",
            media_url=None,
            score=10,
            created_utc=datetime.now(timezone.utc),
        )
        assert p.media_url is None


class TestDownloadResult:
    def test_create_download_result(self, tmp_path: Path):
        e = GoalEvent(
            match_id="1", scorer="X", assist=None, minute=10,
            home_team="A", away_team="B", home_score=1, away_score=0,
            scoring_team="A", aggregate=None, timestamp=datetime.now(timezone.utc),
        )
        d = DownloadResult(
            event=e,
            file_path=tmp_path / "clip.mp4",
            source_url="https://streamff.link/v/1",
            file_size_bytes=500_000,
            duration_seconds=12.5,
        )
        assert d.file_size_bytes == 500_000
        assert d.duration_seconds == 12.5


class TestSendResult:
    def test_successful_send(self, sample_goal_event):
        r = SendResult(
            event=sample_goal_event,
            message_id=42,
            channel_id="@ch",
            success=True,
        )
        assert r.success is True
        assert r.error is None

    def test_failed_send(self, sample_goal_event):
        r = SendResult(
            event=sample_goal_event,
            message_id=0,
            channel_id="@ch",
            success=False,
            error="timeout",
        )
        assert r.success is False
        assert r.error == "timeout"
