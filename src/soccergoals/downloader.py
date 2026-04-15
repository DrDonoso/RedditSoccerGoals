from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx

from soccergoals.config import Config
from soccergoals.models import DownloadResult, GoalEvent, RedditPost

logger = logging.getLogger(__name__)

STREAMFF_VIDEO_RE = re.compile(
    r'(?:source\s+src|file)\s*[=:]\s*["\']?(https?://[^"\'>\s]+\.mp4[^"\'>\s]*)',
    re.IGNORECASE,
)


class MediaDownloader:
    """Downloads goal clip videos from streamff.link or via yt-dlp fallback."""

    def __init__(self, config: Config) -> None:
        self._temp_dir = Path(config.temp_dir)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SoccerGoals/1.0)"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def download(
        self, post: RedditPost, event: GoalEvent
    ) -> DownloadResult | None:
        """Download the video clip. Returns None on failure."""
        media_url = post.media_url
        if not media_url:
            logger.warning("No media URL for post %s", post.post_id)
            return None

        safe_name = re.sub(r"[^\w\-]", "_", f"{event.event_id}_{event.scorer}_{event.minute}")
        dest = self._temp_dir / f"{safe_name}.mp4"

        # Primary: direct download for streamff.link
        if "streamff.link" in media_url:
            result = await self._download_streamff(media_url, dest, event)
            if result:
                return result
            logger.info("Streamff direct download failed, falling back to yt-dlp")

        # Fallback: yt-dlp subprocess
        return await self._download_ytdlp(media_url, dest, event)

    async def _download_streamff(
        self, url: str, dest: Path, event: GoalEvent
    ) -> DownloadResult | None:
        """Download from streamff.link by extracting the video source URL."""
        try:
            page_resp = await self._client.get(url)
            page_resp.raise_for_status()
            page_html = page_resp.text

            match = STREAMFF_VIDEO_RE.search(page_html)
            if not match:
                logger.debug("Could not extract video URL from streamff page")
                return None

            video_url = match.group(1)
            return await self._download_file(video_url, dest, event, url)
        except httpx.HTTPError as exc:
            logger.warning("Streamff page fetch failed: %s", exc)
            return None

    async def _download_file(
        self, video_url: str, dest: Path, event: GoalEvent, source_url: str
    ) -> DownloadResult | None:
        """Stream-download a video file to disk."""
        try:
            async with self._client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            size = dest.stat().st_size
            logger.info("Downloaded %d bytes to %s", size, dest)
            return DownloadResult(
                event=event,
                file_path=dest,
                source_url=source_url,
                file_size_bytes=size,
                duration_seconds=None,
            )
        except httpx.HTTPError as exc:
            logger.warning("Direct file download failed: %s", exc)
            dest.unlink(missing_ok=True)
            return None

    async def _download_ytdlp(
        self, url: str, dest: Path, event: GoalEvent
    ) -> DownloadResult | None:
        """Use yt-dlp subprocess as fallback downloader."""
        output_template = str(dest.with_suffix(""))  # yt-dlp adds extension
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "-f", "best[ext=mp4]/best",
            "-o", f"{output_template}.%(ext)s",
            "--", url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                logger.warning(
                    "yt-dlp failed (code %d): %s",
                    proc.returncode,
                    stderr.decode(errors="replace")[:500],
                )
                return None

            # yt-dlp may use a different extension; find the output file
            downloaded = _find_downloaded_file(output_template)
            if not downloaded:
                logger.warning("yt-dlp completed but output file not found")
                return None

            size = downloaded.stat().st_size
            logger.info("yt-dlp downloaded %d bytes to %s", size, downloaded)
            return DownloadResult(
                event=event,
                file_path=downloaded,
                source_url=url,
                file_size_bytes=size,
                duration_seconds=None,
            )
        except asyncio.TimeoutError:
            logger.warning("yt-dlp timed out for %s", url)
            return None
        except FileNotFoundError:
            logger.error("yt-dlp not found on PATH")
            return None


def _find_downloaded_file(prefix: str) -> Path | None:
    """Find the file yt-dlp actually wrote (extension may vary)."""
    parent = Path(prefix).parent
    stem = Path(prefix).name
    for path in parent.iterdir():
        if path.name.startswith(stem) and path.is_file() and path.stat().st_size > 0:
            return path
    return None
