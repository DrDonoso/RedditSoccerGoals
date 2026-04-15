from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import asyncpraw

from soccergoals.config import Config
from soccergoals.models import GoalEvent, RedditPost

logger = logging.getLogger(__name__)

# Patterns for extracting video URLs from posts
STREAMFF_RE = re.compile(r"https?://(?:www\.)?streamff\.link/\S+", re.IGNORECASE)
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:streamable\.com|v\.redd\.it|streamin\.me|dubz\.link)/\S+",
    re.IGNORECASE,
)


def _extract_media_url(url: str, selftext: str) -> str | None:
    """Extract the best video URL from the post URL or selftext."""
    # Primary: streamff.link
    for text in (url, selftext):
        match = STREAMFF_RE.search(text)
        if match:
            return match.group(0)

    # Fallback: other known video hosts
    for text in (url, selftext):
        match = VIDEO_URL_RE.search(text)
        if match:
            return match.group(0)

    # If the post URL itself looks like a direct video link
    if any(host in url for host in ("streamff.link", "streamable.com", "streamin.me", "dubz.link")):
        return url

    return None


def _build_query(event: GoalEvent) -> str:
    """Build a keyword search query from a GoalEvent."""
    # Use scorer surname + both teams for a targeted search
    parts = event.scorer.split()
    surname = parts[-1] if parts else event.scorer
    return f"{event.home_team} {event.away_team} {surname}"


class RedditSearcher:
    """Searches r/soccer for goal clip posts matching a GoalEvent."""

    def __init__(self, config: Config) -> None:
        self._max_age_minutes = config.max_post_age_minutes
        self._reddit = asyncpraw.Reddit(
            client_id=config.reddit_client_id,
            client_secret=config.reddit_client_secret,
            user_agent=config.reddit_user_agent,
        )

    async def close(self) -> None:
        await self._reddit.close()

    async def search_goal_clip(self, event: GoalEvent) -> list[RedditPost]:
        """Search r/soccer for posts matching the given goal event."""
        query = _build_query(event)
        logger.info("Searching r/soccer: %s", query)

        results: list[RedditPost] = []
        now = datetime.now(timezone.utc)
        cutoff_seconds = self._max_age_minutes * 60

        try:
            subreddit = await self._reddit.subreddit("soccer")
            async for submission in subreddit.search(
                query, sort="new", time_filter="hour", limit=15
            ):
                created = datetime.fromtimestamp(
                    submission.created_utc, tz=timezone.utc
                )
                age_seconds = (now - created).total_seconds()
                if age_seconds > cutoff_seconds:
                    continue

                selftext = getattr(submission, "selftext", "") or ""
                media_url = _extract_media_url(submission.url, selftext)

                post = RedditPost(
                    post_id=submission.id,
                    title=submission.title,
                    url=submission.url,
                    media_url=media_url,
                    score=submission.score,
                    created_utc=created,
                )
                results.append(post)
        except Exception:
            logger.exception("Reddit search failed for query: %s", query)
            return []

        # Prefer posts that have a media URL, then sort by score
        results.sort(key=lambda p: (p.media_url is not None, p.score), reverse=True)

        logger.info(
            "Found %d r/soccer posts (%d with media)",
            len(results),
            sum(1 for r in results if r.media_url),
        )
        return results
