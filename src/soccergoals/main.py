from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from soccergoals.config import Config
from soccergoals.downloader import MediaDownloader
from soccergoals.models import GoalEvent
from soccergoals.poller import MatchPoller
from soccergoals.searcher import RedditSearcher
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
    """Main application loop: poll → search → download → send → record."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._poller = MatchPoller(config)
        self._searcher = RedditSearcher(config)
        self._downloader = MediaDownloader(config)
        self._sender = TelegramSender(config)
        self._store = StateStore(config)
        self._running = True

    async def start(self) -> None:
        """Initialize components and run the main loop."""
        await self._store.init()
        logger.info(
            "SoccerGoals started — monitoring %s, polling every %ds",
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
        await self._poller.close()
        await self._searcher.close()
        await self._downloader.close()
        await self._store.close()
        self._cleanup_temp_files()
        logger.info("Shutdown complete")

    async def _tick(self) -> None:
        """Execute one poll cycle."""
        # 1. Poll live matches for new goals
        new_goals = await self._poller.poll_live_matches(
            self._config.monitored_teams
        )

        # Save poll state with a hash of fixture IDs
        if new_goals:
            ids = sorted(g.match_id for g in new_goals)
            fixtures_hash = hashlib.md5(
                ",".join(ids).encode()
            ).hexdigest()
            await self._store.save_poll_state(fixtures_hash)

        # 2. Process each new goal
        for goal in new_goals:
            if await self._store.is_processed(
                goal.match_id, goal.scorer, goal.minute
            ):
                logger.debug("Skipping already-processed goal: %s %d'", goal.scorer, goal.minute)
                continue
            await self._process_goal(goal)

        # 3. Retry previously failed goals
        await self._retry_failed()

    async def _process_goal(self, goal: GoalEvent) -> None:
        """Full pipeline for a single goal: search → download → send."""
        logger.info(
            "Processing goal: %s %d' — %s %d-%d %s",
            goal.scorer, goal.minute,
            goal.home_team, goal.home_score, goal.away_score, goal.away_team,
        )

        # Search Reddit
        posts = await self._searcher.search_goal_clip(goal)
        post_with_media = next((p for p in posts if p.media_url), None)

        if not post_with_media:
            logger.warning("No clip found for %s %d'", goal.scorer, goal.minute)
            await self._store.record_goal(
                goal.match_id, goal.scorer, goal.minute,
                status="no_clip",
                reddit_post_id=posts[0].post_id if posts else None,
            )
            return

        # Download
        download = await self._downloader.download(post_with_media, goal)
        if not download:
            logger.warning("Download failed for %s %d'", goal.scorer, goal.minute)
            await self._store.record_goal(
                goal.match_id, goal.scorer, goal.minute,
                status="failed",
                reddit_post_id=post_with_media.post_id,
                error_message="Download failed",
            )
            return

        # Send to Telegram
        result = await self._sender.send_goal_clip(download)
        if result.success:
            await self._store.record_goal(
                goal.match_id, goal.scorer, goal.minute,
                status="sent",
                reddit_post_id=post_with_media.post_id,
                file_path=str(download.file_path),
                telegram_msg_id=result.message_id,
            )
        else:
            await self._store.record_goal(
                goal.match_id, goal.scorer, goal.minute,
                status="send_failed",
                reddit_post_id=post_with_media.post_id,
                file_path=str(download.file_path),
                error_message=result.error,
            )

    async def _retry_failed(self) -> None:
        """Retry goals that previously failed, with exponential backoff."""
        pending = await self._store.get_pending_retries(self._config.max_retries)
        if not pending:
            return

        logger.info("Retrying %d previously failed goals", len(pending))
        now = datetime.now(timezone.utc)

        for row in pending:
            retry_count = row["retry_count"]

            # Exponential backoff: wait 2^retry_count poll intervals before retrying
            updated = datetime.fromisoformat(row["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            backoff_seconds = (2 ** retry_count) * self._config.polling_interval
            if (now - updated).total_seconds() < backoff_seconds:
                continue

            # Reconstruct a minimal GoalEvent for retry
            goal = GoalEvent(
                match_id=row["match_id"],
                scorer=row["scorer"],
                assist=None,
                minute=row["minute"],
                home_team="",
                away_team="",
                home_score=0,
                away_score=0,
                scoring_team="",
                aggregate=None,
                timestamp=now,
            )

            logger.info(
                "Retrying goal %s %d' (attempt %d/%d)",
                goal.scorer, goal.minute, retry_count + 1, self._config.max_retries,
            )

            # Re-run the pipeline
            posts = await self._searcher.search_goal_clip(goal)
            post_with_media = next((p for p in posts if p.media_url), None)

            if not post_with_media:
                await self._store.update_status(
                    goal.match_id, goal.scorer, goal.minute,
                    status="no_clip",
                )
                continue

            download = await self._downloader.download(post_with_media, goal)
            if not download:
                await self._store.update_status(
                    goal.match_id, goal.scorer, goal.minute,
                    status="failed",
                    error_message="Download failed on retry",
                )
                continue

            result = await self._sender.send_goal_clip(download)
            if result.success:
                await self._store.record_goal(
                    goal.match_id, goal.scorer, goal.minute,
                    status="sent",
                    reddit_post_id=post_with_media.post_id,
                    telegram_msg_id=result.message_id,
                )
            else:
                await self._store.update_status(
                    goal.match_id, goal.scorer, goal.minute,
                    status="send_failed",
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
