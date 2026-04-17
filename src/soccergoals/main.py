from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from soccergoals.config import Config
from soccergoals.downloader import MediaDownloader
from soccergoals.models import GoalEvent, RedditPost
from soccergoals.scanner import RedditGoalScanner
from soccergoals.sender import TelegramSender
from soccergoals.store import StateStore

logger = logging.getLogger("soccergoals")


def _setup_logging() -> None:
    """Configure structured logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger("soccergoals")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncprawcore").setLevel(logging.WARNING)
    logging.getLogger("asyncpraw").setLevel(logging.WARNING)


class Orchestrator:
    """Main application loop: scan → download → send → record."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._scanner = RedditGoalScanner(config)
        self._downloader = MediaDownloader(config)
        self._sender = TelegramSender(config)
        self._store = StateStore(config)
        self._running = True

    async def start(self) -> None:
        """Initialize components and run the main loop."""
        await self._store.init()
        logger.info(
            "SoccerGoals started — monitoring %s, scanning every %ds",
            ", ".join(self._config.monitored_teams),
            self._config.polling_interval,
        )

        try:
            while self._running:
                await self._tick()
                await asyncio.sleep(self._config.polling_interval)
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Signal the main loop to stop."""
        logger.info("Shutdown requested")
        self._running = False

    async def _shutdown(self) -> None:
        """Clean up all resources."""
        logger.info("Shutting down components...")
        await self._scanner.close()
        await self._downloader.close()
        await self._store.close()
        self._cleanup_temp_files()
        logger.info("Shutdown complete")

    async def _tick(self) -> None:
        """Execute one scan cycle."""
        scan_results = await self._scanner.scan_new_posts(
            self._config.monitored_teams
        )

        for result in scan_results:
            event = result.event
            post = result.post

            # Layer 1 dedup: Reddit post ID
            if await self._store.is_post_seen(post.post_id):
                continue
            await self._store.mark_post_seen(post.post_id)

            # Layer 2 dedup: event hash
            if await self._store.is_processed(
                event.home_team, event.away_team, event.scorer, event.minute
            ):
                logger.debug(
                    "Skipping already-processed goal: %s %d'",
                    event.scorer, event.minute,
                )
                continue

            await self._process_goal(event, post)

        # Retry previously failed goals
        await self._retry_failed()

    async def _process_goal(self, event: GoalEvent, post: RedditPost) -> None:
        """Full pipeline for a single goal: download → send → record."""
        logger.info(
            "Processing goal: %s %d' — %s %d-%d %s",
            event.scorer, event.minute,
            event.home_team, event.home_score, event.away_score, event.away_team,
        )

        if not post.media_url:
            logger.warning("No clip found for %s %d'", event.scorer, event.minute)
            await self._store.record_goal(
                event.event_id, event.home_team, event.away_team,
                event.scorer, event.minute,
                status="no_clip",
                reddit_post_id=post.post_id,
                media_url=post.media_url,
                home_score=event.home_score,
                away_score=event.away_score,
                home_scored=event.home_scored,
                disallowed=event.disallowed,
            )
            return

        # Download
        download = await self._downloader.download(post, event)
        if not download:
            logger.warning("Download failed for %s %d'", event.scorer, event.minute)
            await self._store.record_goal(
                event.event_id, event.home_team, event.away_team,
                event.scorer, event.minute,
                status="failed",
                reddit_post_id=post.post_id,
                media_url=post.media_url,
                error_message="Download failed",
                home_score=event.home_score,
                away_score=event.away_score,
                home_scored=event.home_scored,
                disallowed=event.disallowed,
            )
            return

        # Send to Telegram
        result = await self._sender.send_goal_clip(download)
        if result.success:
            await self._store.record_goal(
                event.event_id, event.home_team, event.away_team,
                event.scorer, event.minute,
                status="sent",
                reddit_post_id=post.post_id,
                media_url=post.media_url,
                file_path=str(download.file_path),
                telegram_msg_id=result.message_id,
                home_score=event.home_score,
                away_score=event.away_score,
                home_scored=event.home_scored,
                disallowed=event.disallowed,
            )
        else:
            await self._store.record_goal(
                event.event_id, event.home_team, event.away_team,
                event.scorer, event.minute,
                status="send_failed",
                reddit_post_id=post.post_id,
                media_url=post.media_url,
                file_path=str(download.file_path),
                error_message=result.error,
                home_score=event.home_score,
                away_score=event.away_score,
                home_scored=event.home_scored,
                disallowed=event.disallowed,
            )

    async def _retry_failed(self) -> None:
        """Retry goals that previously failed, with exponential backoff.

        Uses stored media_url instead of re-scanning Reddit, so retries
        work even after the original post has aged off r/soccer/new.
        """
        pending = await self._store.get_pending_retries(self._config.max_retries)
        if not pending:
            return

        logger.info("Retrying %d previously failed goals", len(pending))
        now = datetime.now(timezone.utc)

        for row in pending:
            retry_count = row["retry_count"]
            event_hash = row["event_hash"]

            # Exponential backoff: wait 2^retry_count poll intervals before retrying
            updated = datetime.fromisoformat(row["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            backoff_seconds = (2 ** retry_count) * self._config.polling_interval
            if (now - updated).total_seconds() < backoff_seconds:
                continue

            event_id = row["event_id"]
            scorer = row["scorer"]
            minute = row["minute"]
            home_team = row.get("home_team") or ""
            away_team = row.get("away_team") or ""
            media_url = row.get("media_url")

            logger.info(
                "Retrying goal %s %d' (attempt %d/%d)",
                scorer, minute, retry_count + 1, self._config.max_retries,
            )

            # If we have no media URL, bump retry count and move on
            if not media_url:
                logger.info(
                    "No media URL stored for %s %d', bumping retry count",
                    scorer, minute,
                )
                await self._store.bump_retry(
                    event_hash, status="no_clip",
                    error_message="No media URL available for retry",
                )
                continue

            # Build lightweight objects from stored data for re-download
            post = RedditPost(
                post_id=row.get("reddit_post_id") or "",
                title="",
                url=media_url,
                media_url=media_url,
                score=0,
                created_utc=now,
            )
            event = GoalEvent(
                event_id=event_id,
                scorer=scorer,
                minute=minute,
                home_team=home_team,
                away_team=away_team,
                home_score=row.get("home_score") or 0,
                away_score=row.get("away_score") or 0,
                timestamp=now,
                home_scored=None if row.get("home_scored") is None else bool(row["home_scored"]),
                disallowed=bool(row.get("disallowed", 0)),
            )

            download = await self._downloader.download(post, event)
            if not download:
                await self._store.bump_retry(
                    event_hash, status="failed",
                    error_message="Download failed on retry",
                )
                continue

            result = await self._sender.send_goal_clip(download)
            if result.success:
                await self._store.record_goal(
                    event_id, home_team, away_team,
                    scorer, minute,
                    status="sent",
                    reddit_post_id=row.get("reddit_post_id"),
                    media_url=media_url,
                    telegram_msg_id=result.message_id,
                )
            else:
                await self._store.bump_retry(
                    event_hash, status="send_failed",
                    error_message=result.error,
                )

    def _cleanup_temp_files(self) -> None:
        """Remove any leftover temporary files."""
        temp_dir = Path(self._config.temp_dir)
        if not temp_dir.exists():
            return
        for f in temp_dir.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
                logger.debug("Cleaned up temp file: %s", f)


def main() -> None:
    """Application entry point."""
    _setup_logging()
    config = Config()

    orchestrator = Orchestrator(config)
    loop = asyncio.new_event_loop()

    def _handle_signal() -> None:
        loop.create_task(orchestrator.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # signal.SIGTERM not supported on Windows — use KeyboardInterrupt
            pass

    try:
        loop.run_until_complete(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        loop.run_until_complete(orchestrator.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
