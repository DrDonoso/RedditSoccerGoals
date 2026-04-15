from __future__ import annotations

import hashlib
import logging
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from soccergoals.config import Config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_goals (
    id              INTEGER PRIMARY KEY,
    event_id        TEXT NOT NULL,
    event_hash      TEXT UNIQUE NOT NULL,
    scorer          TEXT NOT NULL,
    minute          INTEGER NOT NULL,
    status          TEXT NOT NULL,
    reddit_post_id  TEXT,
    file_path       TEXT,
    telegram_msg_id INTEGER,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seen_posts (
    post_id     TEXT PRIMARY KEY,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _strip_accents(text: str) -> str:
    """Remove diacritics/accents from text."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Lowercase, strip accents."""
    return _strip_accents(text.strip()).lower()


def _extract_surname(name: str) -> str:
    """Extract the last name from a full name."""
    parts = name.strip().split()
    return parts[-1] if parts else name


def _event_hash(home_team: str, away_team: str, scorer: str, minute: int) -> str:
    """Compute a dedup hash for a goal event."""
    raw = "|".join([
        _normalize(home_team),
        _normalize(away_team),
        _normalize(_extract_surname(scorer)),
        str(minute),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class StateStore:
    """SQLite-backed state store for tracking processed goals."""

    def __init__(self, config: Config) -> None:
        self._db_path = config.db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open the database and create tables if needed."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("State store initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def is_post_seen(self, post_id: str) -> bool:
        """Check if a Reddit post has already been seen (layer 1 dedup)."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT 1 FROM seen_posts WHERE post_id = ?", (post_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_post_seen(self, post_id: str) -> None:
        """Record a Reddit post as seen."""
        assert self._db is not None
        await self._db.execute(
            "INSERT OR IGNORE INTO seen_posts (post_id) VALUES (?)", (post_id,)
        )
        await self._db.commit()

    async def is_processed(self, home_team: str, away_team: str, scorer: str, minute: int) -> bool:
        """Check if a goal has already been processed (any status)."""
        h = _event_hash(home_team, away_team, scorer, minute)
        assert self._db is not None
        async with self._db.execute(
            "SELECT 1 FROM processed_goals WHERE event_hash = ?", (h,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def record_goal(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        scorer: str,
        minute: int,
        status: str,
        reddit_post_id: str | None = None,
        file_path: str | None = None,
        telegram_msg_id: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Insert or update a goal record."""
        h = _event_hash(home_team, away_team, scorer, minute)
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO processed_goals
                (event_id, event_hash, scorer, minute, status,
                 reddit_post_id, file_path, telegram_msg_id, error_message,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_hash) DO UPDATE SET
                status = excluded.status,
                reddit_post_id = COALESCE(excluded.reddit_post_id, reddit_post_id),
                file_path = COALESCE(excluded.file_path, file_path),
                telegram_msg_id = COALESCE(excluded.telegram_msg_id, telegram_msg_id),
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (event_id, h, scorer, minute, status,
             reddit_post_id, file_path, telegram_msg_id, error_message,
             now, now),
        )
        await self._db.commit()

    async def update_status(
        self,
        home_team: str,
        away_team: str,
        scorer: str,
        minute: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Update the status of an existing goal record."""
        h = _event_hash(home_team, away_team, scorer, minute)
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            UPDATE processed_goals
            SET status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = ?
            WHERE event_hash = ?
            """,
            (status, error_message, now, h),
        )
        await self._db.commit()

    async def get_pending_retries(self, max_retries: int) -> list[dict]:
        """Get goals that failed and are eligible for retry."""
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT event_id, scorer, minute, status, retry_count,
                   error_message, updated_at, reddit_post_id
            FROM processed_goals
            WHERE status IN ('failed', 'send_failed', 'no_clip')
              AND retry_count < ?
            ORDER BY created_at ASC
            """,
            (max_retries,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
