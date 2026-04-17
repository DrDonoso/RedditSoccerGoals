from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from telegram import Bot

from soccergoals.config import Config
from soccergoals.models import DownloadResult, SendResult

logger = logging.getLogger(__name__)

TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB
_COMPRESS_TARGET_BYTES = 49 * 1024 * 1024  # Target slightly under limit


async def _probe_video(path: Path) -> dict[str, int]:
    """Use ffprobe to extract width, height, and duration from a video file."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return {}
        data = json.loads(stdout)
        stream = data.get("streams", [{}])[0]
        result: dict[str, int] = {}
        if "width" in stream:
            result["width"] = int(stream["width"])
        if "height" in stream:
            result["height"] = int(stream["height"])
        duration = stream.get("duration") or data.get("format", {}).get("duration")
        if duration:
            result["duration"] = int(float(duration))
        return result
    except Exception:
        logger.debug("ffprobe failed, sending without dimensions")
        return {}


async def _probe_duration(path: Path) -> float | None:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return None
        data = json.loads(stdout)
        dur = data.get("format", {}).get("duration")
        return float(dur) if dur else None
    except Exception:
        return None


async def _compress_video(source: Path) -> Path | None:
    """Re-encode video to fit under Telegram's file size limit."""
    duration = await _probe_duration(source)
    if not duration or duration <= 0:
        logger.warning("Cannot compress: unable to determine duration")
        return None

    # Calculate VIDEO bitrate budget: total budget minus audio (128kbps),
    # with 10% safety margin for container/muxing overhead.
    audio_kbps = 128
    total_kbps = int((_COMPRESS_TARGET_BYTES * 8) / duration / 1000)
    video_kbps = int((total_kbps - audio_kbps) * 0.90)

    # Don't bother if we'd need an absurdly low bitrate
    if video_kbps < 200:
        logger.warning("Video too long to compress under Telegram limit (would need %dk)", video_kbps)
        return None

    compressed = source.with_stem(source.stem + "_compressed")
    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-c:v", "libx264", "-preset", "slow",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        str(compressed),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            logger.warning("ffmpeg compression failed: %s", stderr.decode(errors="replace")[:300])
            compressed.unlink(missing_ok=True)
            return None

        size = compressed.stat().st_size
        logger.info("Compressed %s: %d → %d bytes", source.name, source.stat().st_size, size)
        if size > TELEGRAM_FILE_LIMIT:
            logger.warning("Compressed file still too large (%d bytes)", size)
            compressed.unlink(missing_ok=True)
            return None
        return compressed
    except asyncio.TimeoutError:
        logger.warning("ffmpeg compression timed out")
        compressed.unlink(missing_ok=True)
        return None
    except FileNotFoundError:
        logger.error("ffmpeg not found on PATH")
        return None


class TelegramSender:
    """Sends goal clip videos to a Telegram channel."""

    def __init__(self, config: Config) -> None:
        self._bot = Bot(token=config.telegram_bot_token)
        self._channel_id = config.telegram_channel_id

    async def send_goal_clip(self, download: DownloadResult) -> SendResult:
        """Send the downloaded video to the configured Telegram channel."""
        event = download.event

        # Build score string with brackets around the scoring team
        score_str = f"{event.home_score}-{event.away_score}"
        if not event.disallowed and event.home_scored is not None:
            if event.home_scored:
                score_str = f"[{event.home_score}]-{event.away_score}"
            else:
                score_str = f"{event.home_score}-[{event.away_score}]"

        disallowed_prefix = "\U0001f6a9Disallowed!! " if event.disallowed else ""
        caption = (
            f"\u26bd {disallowed_prefix}{event.scorer} {event.minute}' \u2014 "
            f"{event.home_team} {score_str} {event.away_team}"
        )

        if download.file_size_bytes > TELEGRAM_FILE_LIMIT:
            logger.info(
                "File too large (%d bytes), attempting compression...",
                download.file_size_bytes,
            )
            compressed = await _compress_video(download.file_path)
            if compressed is None:
                msg = (
                    f"File too large for Telegram ({download.file_size_bytes} bytes, "
                    f"limit {TELEGRAM_FILE_LIMIT} bytes) and compression failed"
                )
                logger.warning(msg)
                return SendResult(
                    event=event,
                    message_id=0,
                    channel_id=self._channel_id,
                    success=False,
                    error=msg,
                )
            # Use the compressed file instead
            download = DownloadResult(
                event=download.event,
                file_path=compressed,
                source_url=download.source_url,
                file_size_bytes=compressed.stat().st_size,
                duration_seconds=download.duration_seconds,
            )

        try:
            video_meta = await _probe_video(download.file_path)

            with open(download.file_path, "rb") as video_file:
                message = await self._bot.send_video(
                    chat_id=self._channel_id,
                    video=video_file,
                    caption=caption,
                    supports_streaming=True,
                    **video_meta,
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
