from __future__ import annotations

import pytest

from soccergoals.store import StateStore, _event_hash


@pytest.fixture()
async def store(config):
    s = StateStore(config)
    await s.init()
    yield s
    await s.close()


class TestEventHash:
    def test_deterministic(self):
        h1 = _event_hash("1", "Messi", 45)
        h2 = _event_hash("1", "Messi", 45)
        assert h1 == h2

    def test_different_for_different_events(self):
        h1 = _event_hash("1", "Messi", 45)
        h2 = _event_hash("1", "Messi", 46)
        assert h1 != h2

    def test_length(self):
        h = _event_hash("1", "X", 10)
        assert len(h) == 32


class TestTableCreation:
    async def test_init_creates_tables(self, store):
        assert store._db is not None
        async with store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        assert "processed_goals" in tables
        assert "poll_state" in tables


class TestGoalDedup:
    async def test_new_goal_not_processed(self, store):
        assert await store.is_processed("1", "Messi", 45) is False

    async def test_recorded_goal_is_processed(self, store):
        await store.record_goal("1", "Messi", 45, status="sent")
        assert await store.is_processed("1", "Messi", 45) is True

    async def test_different_minute_not_processed(self, store):
        await store.record_goal("1", "Messi", 45, status="sent")
        assert await store.is_processed("1", "Messi", 50) is False

    async def test_upsert_same_event(self, store):
        """Recording the same event twice should upsert, not duplicate."""
        await store.record_goal("1", "Messi", 45, status="no_clip")
        await store.record_goal("1", "Messi", 45, status="sent", telegram_msg_id=42)

        assert await store.is_processed("1", "Messi", 45) is True
        async with store._db.execute(
            "SELECT status, telegram_msg_id FROM processed_goals WHERE event_hash = ?",
            (_event_hash("1", "Messi", 45),),
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == "sent"
        assert row[1] == 42


class TestStatusTransitions:
    async def test_update_status_increments_retry(self, store):
        await store.record_goal("1", "X", 10, status="failed", error_message="Download failed")
        await store.update_status("1", "X", 10, status="failed", error_message="Retry failed")

        async with store._db.execute(
            "SELECT retry_count, error_message FROM processed_goals WHERE event_hash = ?",
            (_event_hash("1", "X", 10),),
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == 1
        assert row[1] == "Retry failed"

    async def test_transition_failed_to_sent(self, store):
        await store.record_goal("1", "X", 10, status="failed")
        await store.record_goal("1", "X", 10, status="sent", telegram_msg_id=99)

        async with store._db.execute(
            "SELECT status FROM processed_goals WHERE event_hash = ?",
            (_event_hash("1", "X", 10),),
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == "sent"


class TestPendingRetries:
    async def test_failed_goal_appears_in_retries(self, store):
        await store.record_goal("1", "X", 10, status="failed", error_message="err")
        pending = await store.get_pending_retries(max_retries=3)
        assert len(pending) == 1
        assert pending[0]["scorer"] == "X"

    async def test_sent_goal_not_in_retries(self, store):
        await store.record_goal("1", "X", 10, status="sent")
        pending = await store.get_pending_retries(max_retries=3)
        assert len(pending) == 0

    async def test_exceeded_retries_excluded(self, store):
        await store.record_goal("1", "X", 10, status="failed")
        # Bump retry_count to 3
        for _ in range(3):
            await store.update_status("1", "X", 10, status="failed")

        pending = await store.get_pending_retries(max_retries=3)
        assert len(pending) == 0

    async def test_multiple_statuses_in_retries(self, store):
        await store.record_goal("1", "A", 10, status="failed")
        await store.record_goal("2", "B", 20, status="send_failed")
        await store.record_goal("3", "C", 30, status="no_clip")
        await store.record_goal("4", "D", 40, status="sent")

        pending = await store.get_pending_retries(max_retries=3)
        scorers = {p["scorer"] for p in pending}
        assert scorers == {"A", "B", "C"}


class TestPollState:
    async def test_save_and_overwrite_poll_state(self, store):
        await store.save_poll_state("hash1")
        await store.save_poll_state("hash2")

        async with store._db.execute(
            "SELECT fixtures_hash FROM poll_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == "hash2"

    async def test_poll_state_has_timestamp(self, store):
        await store.save_poll_state("abc")

        async with store._db.execute(
            "SELECT last_poll_at FROM poll_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] is not None
