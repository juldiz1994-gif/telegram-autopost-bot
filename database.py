import logging
from datetime import datetime
from typing import Any, Optional

import asyncpg

from config import config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
        logger.info("Database pool created")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("Database pool closed")

    async def init_db(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS content_plan (
                    id SERIAL PRIMARY KEY,
                    niche TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    format TEXT NOT NULL,
                    description TEXT NOT NULL,
                    scheduled_date DATE NOT NULL,
                    scheduled_time TIME NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER REFERENCES content_plan(id),
                    text TEXT NOT NULL,
                    image_prompt TEXT,
                    image_path TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    message_id BIGINT,
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        logger.info("Database tables initialized")

    async def save_plan(self, niche: str, items: list[dict]) -> list[int]:
        ids = []
        async with self._pool.acquire() as conn:
            for item in items:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO content_plan (niche, topic, format, description, scheduled_date, scheduled_time)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    niche,
                    item["topic"],
                    item["format"],
                    item["description"],
                    item["scheduled_date"],
                    item["scheduled_time"],
                )
                ids.append(row_id)
        logger.info("Saved %d plan items", len(ids))
        return ids

    async def get_plan(self, plan_id: Optional[int] = None) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            if plan_id:
                return await conn.fetch(
                    "SELECT * FROM content_plan WHERE id >= $1 ORDER BY scheduled_date, scheduled_time",
                    plan_id,
                )
            return await conn.fetch("""
                SELECT * FROM (
                    SELECT * FROM content_plan ORDER BY created_at DESC LIMIT 7
                ) sub ORDER BY scheduled_date, scheduled_time
            """)

    async def save_post(self, plan_id: int, text: str, image_prompt: str) -> int:
        async with self._pool.acquire() as conn:
            post_id = await conn.fetchval(
                """
                INSERT INTO posts (plan_id, text, image_prompt, status)
                VALUES ($1, $2, $3, 'draft')
                RETURNING id
                """,
                plan_id,
                text,
                image_prompt,
            )
        logger.info("Saved post id=%d", post_id)
        return post_id

    async def update_post_status(self, post_id: int, status: str, **fields: Any) -> None:
        allowed = {"message_id", "published_at", "text", "image_path"}
        updates: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed}
        updates["status"] = status
        keys = list(updates.keys())
        values = list(updates.values())
        set_clauses = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(keys))
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE posts SET {set_clauses} WHERE id = $1",
                post_id,
                *values,
            )

    async def get_posts_by_status(self, status: str) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.*, cp.topic, cp.format, cp.scheduled_date, cp.scheduled_time
                FROM posts p
                LEFT JOIN content_plan cp ON p.plan_id = cp.id
                WHERE p.status = $1
                ORDER BY cp.scheduled_date, cp.scheduled_time
                """,
                status,
            )

    async def get_next_post(self, status: str) -> Optional[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT p.*, cp.topic, cp.format, cp.scheduled_date, cp.scheduled_time
                FROM posts p
                LEFT JOIN content_plan cp ON p.plan_id = cp.id
                WHERE p.status = $1
                ORDER BY p.id ASC
                LIMIT 1
                """,
                status,
            )

    async def get_stats(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT status, COUNT(*) AS cnt FROM posts GROUP BY status")
        return {row["status"]: row["cnt"] for row in rows}

    async def get_post_by_id(self, post_id: int) -> Optional[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT p.*, cp.topic, cp.format, cp.scheduled_date, cp.scheduled_time
                FROM posts p
                LEFT JOIN content_plan cp ON p.plan_id = cp.id
                WHERE p.id = $1
                """,
                post_id,
            )


db = Database()
