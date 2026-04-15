from __future__ import annotations

import logging

from telegram import Bot

from soccergoals.config import Config
from soccergoals.models import DownloadResult, SendResult

logger = logging.getLogger(__name__)

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB


class TelegramSender:
    """Sends goal clip videos to a Telegram channel."""

    def __init__(self, config: Config) -> None:
        self._bot = Bot(token=config.telegram_bot_token)
        self._channel_id = config.telegram_channel_id

    async def send_goal_clip(self, download: DownloadResult) -> SendResult:
        """Send the downloaded video to the configured Telegram channel."""
        event = download.event
        caption = (
            f"\u26bd {event.scorer} {event.minute}' \u2014 "
            f"{event.home_team} {event.home_score}-{event.away_score} {event.away_team}"
        )

        if download.file_size_bytes > TELEGRAM_FILE_LIMIT:
            msg = (
                f"File too large for Telegram ({download.file_size_bytes} bytes, "
                f"limit {TELEGRAM_FILE_LIMIT} bytes)"
            )
            logger.warning(msg)
            return SendResult(
                event=event,
                message_id=0,
                channel_id=self._channel_id,
                success=False,
                error=msg,
            )

        try:
            with open(download.file_path, "rb") as video_file:
                message = await self._bot.send_video(
                    chat_id=self._channel_id,
                    video=video_file,
                    caption=caption,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=30,
                )

            logger.info(
                "Sent goal clip to Telegram: message_id=%d channel=%s",
                message.message_id,
                self._channel_id,
            )
            return SendResult(
                event=event,
                message_id=message.message_id,
                channel_id=self._channel_id,
                success=True,
            )
        except Exception as exc:
            logger.exception("Telegram send failed")
            return SendResult(
                event=event,
                message_id=0,
                channel_id=self._channel_id,
                success=False,
                error=str(exc),
            )
        finally:
            # Clean up temp file after send attempt
            download.file_path.unlink(missing_ok=True)
