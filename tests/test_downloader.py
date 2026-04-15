from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from soccergoals.downloader import MediaDownloader
from soccergoals.models import DownloadResult, GoalEvent, RedditPost


@pytest.fixture()
def downloader(config):
    d = MediaDownloader(config)
    yield d


@pytest.fixture()
def event(sample_goal_event) -> GoalEvent:
    return sample_goal_event


@pytest.fixture()
def post_streamff() -> RedditPost:
    from datetime import datetime, timezone

    return RedditPost(
        post_id="p1",
        title="Goal clip",
        url="https://streamff.link/v/abc",
        media_url="https://streamff.link/v/abc",
        score=100,
        created_utc=datetime.now(timezone.utc),
    )


@pytest.fixture()
def post_other() -> RedditPost:
    from datetime import datetime, timezone

    return RedditPost(
        post_id="p2",
        title="Goal clip",
        url="https://streamable.com/xyz",
        media_url="https://streamable.com/xyz",
        score=50,
        created_utc=datetime.now(timezone.utc),
    )


class TestStreamffDownload:
    async def test_successful_streamff_download(self, downloader, event, post_streamff):
        page_html = '<source src="https://cdn.streamff.link/uploads/abc.mp4" type="video/mp4">'

        page_resp = httpx.Response(200, text=page_html, request=httpx.Request("GET", "https://streamff.link/v/abc"))

        # Build an async-context-manager that `.stream()` returns
        video_bytes = b"\x00\x00\x01\xb3" + b"\x00" * 1000

        async def aiter_bytes(chunk_size=65536):
            yield video_bytes

        stream_resp = AsyncMock()
        stream_resp.raise_for_status = MagicMock()
        stream_resp.aiter_bytes = aiter_bytes

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(downloader._client, "get", new_callable=AsyncMock, return_value=page_resp):
            with patch.object(downloader._client, "stream", return_value=stream_cm):
                result = await downloader.download(post_streamff, event)

        assert result is not None
        assert result.file_path.exists()
        assert result.file_size_bytes > 0

    async def test_streamff_no_video_url_falls_back_to_ytdlp(self, downloader, event, post_streamff):
        page_html = "<html><body>No video here</body></html>"
        page_resp = httpx.Response(200, text=page_html, request=httpx.Request("GET", "https://streamff.link/v/abc"))

        with patch.object(downloader._client, "get", new_callable=AsyncMock, return_value=page_resp):
            with patch.object(downloader, "_download_ytdlp", new_callable=AsyncMock, return_value=None) as mock_yt:
                result = await downloader.download(post_streamff, event)

        mock_yt.assert_awaited_once()
        assert result is None

    async def test_streamff_page_fetch_error(self, downloader, event, post_streamff):
        with patch.object(
            downloader._client, "get", new_callable=AsyncMock,
            side_effect=httpx.ConnectTimeout("timeout"),
        ):
            with patch.object(downloader, "_download_ytdlp", new_callable=AsyncMock, return_value=None) as mock_yt:
                result = await downloader.download(post_streamff, event)

        mock_yt.assert_awaited_once()
        assert result is None


class TestYtdlpFallback:
    async def test_ytdlp_success(self, downloader, event, post_other, tmp_path):
        """yt-dlp returns 0 and a file is found."""
        # Create a fake output file where yt-dlp would put it
        safe_name = f"{event.event_id}_{event.scorer}_{event.minute}".replace(" ", "_")
        # The downloader will construct the path from _temp_dir
        # We mock the subprocess instead

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("soccergoals.downloader._find_downloaded_file") as mock_find:
                fake_file = tmp_path / "clip.mp4"
                fake_file.write_bytes(b"\x00" * 2048)
                mock_find.return_value = fake_file
                result = await downloader._download_ytdlp(post_other.media_url, tmp_path / "out", event)

        assert result is not None
        assert result.file_size_bytes == 2048

    async def test_ytdlp_failure(self, downloader, event, post_other, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error msg"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await downloader._download_ytdlp(post_other.media_url, tmp_path / "out", event)

        assert result is None

    async def test_ytdlp_not_found(self, downloader, event, post_other, tmp_path):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError):
            result = await downloader._download_ytdlp(post_other.media_url, tmp_path / "out", event)

        assert result is None


class TestNoMedia:
    async def test_no_media_url_returns_none(self, downloader, event):
        from datetime import datetime, timezone

        post = RedditPost(
            post_id="no", title="No media", url="https://reddit.com",
            media_url=None, score=10, created_utc=datetime.now(timezone.utc),
        )
        result = await downloader.download(post, event)
        assert result is None
