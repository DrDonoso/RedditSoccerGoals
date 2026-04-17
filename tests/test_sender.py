from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soccergoals.models import DownloadResult, GoalEvent, SendResult
from soccergoals.sender import TELEGRAM_FILE_LIMIT, TelegramSender


@pytest.fixture()
def sender(config):
    with patch("soccergoals.sender.Bot") as MockBot:
        mock_bot = AsyncMock()
        MockBot.return_value = mock_bot
        s = TelegramSender(config)
        s._bot = mock_bot
        yield s


class TestCaptionFormatting:
    async def test_caption_contains_scorer_and_teams(self, sender, sample_download_result):
        mock_msg = MagicMock()
        mock_msg.message_id = 100
        sender._bot.send_video = AsyncMock(return_value=mock_msg)

        result = await sender.send_goal_clip(sample_download_result)

        # Inspect the caption passed to send_video
        call_kwargs = sender._bot.send_video.call_args.kwargs
        caption = call_kwargs["caption"]
        assert "Vinícius Júnior" in caption
        assert "23'" in caption
        assert "Real Madrid" in caption
        assert "Barcelona" in caption
        assert "[1]-0" in caption


class TestSuccessfulSend:
    async def test_send_returns_success(self, sender, sample_download_result):
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        sender._bot.send_video = AsyncMock(return_value=mock_msg)

        result = await sender.send_goal_clip(sample_download_result)

        assert result.success is True
        assert result.message_id == 42
        assert result.error is None

    async def test_send_calls_bot_correctly(self, sender, sample_download_result):
        mock_msg = MagicMock()
        mock_msg.message_id = 1
        sender._bot.send_video = AsyncMock(return_value=mock_msg)

        await sender.send_goal_clip(sample_download_result)

        sender._bot.send_video.assert_awaited_once()
        call_kwargs = sender._bot.send_video.call_args.kwargs
        assert call_kwargs["chat_id"] == "@test_channel"


class TestSendFailure:
    async def test_exception_returns_failure(self, sender, sample_download_result):
        sender._bot.send_video = AsyncMock(side_effect=Exception("Network error"))

        result = await sender.send_goal_clip(sample_download_result)

        assert result.success is False
        assert "Network error" in result.error
        assert result.message_id == 0


class TestFileSizeLimit:
    async def test_oversized_file_compressed_and_sent(self, sender, sample_goal_event, tmp_path):
        big_file = tmp_path / "big.mp4"
        big_file.write_bytes(b"\x00" * 100)

        compressed_file = tmp_path / "big_compressed.mp4"
        compressed_file.write_bytes(b"\x00" * 50)

        download = DownloadResult(
            event=sample_goal_event,
            file_path=big_file,
            source_url="https://example.com/big.mp4",
            file_size_bytes=TELEGRAM_FILE_LIMIT + 1,
            duration_seconds=None,
        )
        mock_msg = MagicMock()
        mock_msg.message_id = 99
        sender._bot.send_video = AsyncMock(return_value=mock_msg)

        with patch("soccergoals.sender._compress_video", return_value=compressed_file):
            result = await sender.send_goal_clip(download)

        assert result.success is True
        assert result.message_id == 99

    async def test_oversized_file_compression_fails(self, sender, sample_goal_event, tmp_path):
        big_file = tmp_path / "big.mp4"
        big_file.write_bytes(b"\x00" * 100)

        download = DownloadResult(
            event=sample_goal_event,
            file_path=big_file,
            source_url="https://example.com/big.mp4",
            file_size_bytes=TELEGRAM_FILE_LIMIT + 1,
            duration_seconds=None,
        )

        with patch("soccergoals.sender._compress_video", return_value=None):
            result = await sender.send_goal_clip(download)

        assert result.success is False
        assert "compression failed" in result.error.lower()
        sender._bot.send_video.assert_not_awaited()

    async def test_under_limit_sends_normally(self, sender, sample_download_result):
        assert sample_download_result.file_size_bytes < TELEGRAM_FILE_LIMIT
        mock_msg = MagicMock()
        mock_msg.message_id = 1
        sender._bot.send_video = AsyncMock(return_value=mock_msg)

        result = await sender.send_goal_clip(sample_download_result)
        assert result.success is True
