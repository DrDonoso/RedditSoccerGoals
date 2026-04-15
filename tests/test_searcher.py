from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soccergoals.models import GoalEvent
from soccergoals.searcher import RedditSearcher, _build_query, _extract_media_url


# ── Unit helpers ────────────────────────────────────────────────────

class TestBuildQuery:
    def test_uses_surname_and_teams(self):
        event = GoalEvent(
            match_id="1", scorer="Vinícius Júnior", assist=None, minute=10,
            home_team="Real Madrid", away_team="Barcelona", home_score=1,
            away_score=0, scoring_team="Real Madrid", aggregate=None,
            timestamp=datetime.now(timezone.utc),
        )
        q = _build_query(event)
        assert "Júnior" in q
        assert "Real Madrid" in q
        assert "Barcelona" in q

    def test_single_name_scorer(self):
        event = GoalEvent(
            match_id="2", scorer="Pelé", assist=None, minute=5,
            home_team="A", away_team="B", home_score=1, away_score=0,
            scoring_team="A", aggregate=None, timestamp=datetime.now(timezone.utc),
        )
        q = _build_query(event)
        assert "Pelé" in q


class TestExtractMediaUrl:
    def test_streamff_in_url(self):
        url = "https://streamff.link/v/abc123"
        assert _extract_media_url(url, "") == url

    def test_streamff_in_selftext(self):
        result = _extract_media_url(
            "https://reddit.com/post",
            "Check this: https://streamff.link/v/xyz mirror",
        )
        assert result == "https://streamff.link/v/xyz"

    def test_streamable_fallback(self):
        result = _extract_media_url("https://streamable.com/xyz123", "")
        assert result == "https://streamable.com/xyz123"

    def test_no_media_returns_none(self):
        assert _extract_media_url("https://reddit.com/r/soccer/comments/xxx", "") is None

    def test_streamff_preferred_over_streamable(self):
        selftext = (
            "https://streamable.com/old "
            "https://streamff.link/v/new"
        )
        result = _extract_media_url("https://reddit.com/post", selftext)
        assert "streamff.link" in result

    def test_dubz_link_extraction(self):
        result = _extract_media_url("https://dubz.link/v/abc456", "")
        assert result == "https://dubz.link/v/abc456"


# ── RedditSearcher integration ──────────────────────────────────────

def _make_submission(
    post_id: str,
    title: str,
    url: str,
    selftext: str = "",
    score: int = 50,
    age_seconds: float = 60.0,
):
    """Create a mock Reddit submission."""
    now_ts = datetime.now(timezone.utc).timestamp()
    sub = MagicMock()
    sub.id = post_id
    sub.title = title
    sub.url = url
    sub.selftext = selftext
    sub.score = score
    sub.created_utc = now_ts - age_seconds
    return sub


class TestRedditSearcher:
    @pytest.fixture()
    def event(self) -> GoalEvent:
        return GoalEvent(
            match_id="1", scorer="Vinícius Júnior", assist=None, minute=23,
            home_team="Real Madrid", away_team="Barcelona", home_score=1,
            away_score=0, scoring_team="Real Madrid", aggregate=None,
            timestamp=datetime.now(timezone.utc),
        )

    @pytest.fixture()
    def searcher(self, config):
        with patch("soccergoals.searcher.asyncpraw.Reddit"):
            s = RedditSearcher(config)
            yield s

    async def test_finds_clip_with_brackets_title(self, searcher, event):
        sub = _make_submission(
            "p1",
            "Real Madrid [1] - 0 Barcelona - Vinícius Júnior 23'",
            "https://streamff.link/v/abc123",
        )
        mock_subreddit = AsyncMock()
        mock_subreddit.search = _async_iter_from([sub])
        searcher._reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        posts = await searcher.search_goal_clip(event)
        assert len(posts) == 1
        assert posts[0].media_url == "https://streamff.link/v/abc123"

    async def test_finds_clip_without_brackets_title(self, searcher, event):
        sub = _make_submission(
            "p2",
            "Real Madrid 1-0 Barcelona - Vinícius Júnior 23'",
            "https://streamff.link/v/def456",
        )
        mock_subreddit = AsyncMock()
        mock_subreddit.search = _async_iter_from([sub])
        searcher._reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        posts = await searcher.search_goal_clip(event)
        assert len(posts) == 1

    async def test_no_results(self, searcher, event):
        mock_subreddit = AsyncMock()
        mock_subreddit.search = _async_iter_from([])
        searcher._reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        posts = await searcher.search_goal_clip(event)
        assert posts == []

    async def test_filters_old_posts(self, searcher, event):
        # Post 30 minutes old, config says max 15
        old_sub = _make_submission(
            "p3", "Old goal", "https://streamff.link/v/old",
            age_seconds=60 * 20,  # 20 minutes old > max 15
        )
        mock_subreddit = AsyncMock()
        mock_subreddit.search = _async_iter_from([old_sub])
        searcher._reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        posts = await searcher.search_goal_clip(event)
        assert posts == []

    async def test_prefers_posts_with_media(self, searcher, event):
        no_media = _make_submission("p4", "Text post", "https://reddit.com/text", score=200)
        with_media = _make_submission(
            "p5", "Video post", "https://streamff.link/v/vid", score=50,
        )
        mock_subreddit = AsyncMock()
        mock_subreddit.search = _async_iter_from([no_media, with_media])
        searcher._reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        posts = await searcher.search_goal_clip(event)
        assert len(posts) == 2
        # Post with media should be first despite lower score
        assert posts[0].media_url is not None

    async def test_reddit_exception_returns_empty(self, searcher, event):
        searcher._reddit.subreddit = AsyncMock(side_effect=Exception("Reddit down"))

        posts = await searcher.search_goal_clip(event)
        assert posts == []


# ── Helpers ─────────────────────────────────────────────────────────

def _async_iter_from(items):
    """Return a callable that yields items as an async iterator (for subreddit.search)."""
    async def _search(*args, **kwargs):
        for item in items:
            yield item
    return _search
