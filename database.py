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
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    niche TEXT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    channel_title TEXT,
                    post_frequency INTEGER DEFAULT 2,
                    publish_times TEXT DEFAULT '10:00,18:00',
                    status TEXT NOT NULL DEFAULT 'trial',
                    trial_ends_at TIMESTAMP NOT NULL,
                    subscription_ends_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    amount INTEGER DEFAULT 990,
                    status TEXT NOT NULL DEFAULT 'pending',
                    check_file_id TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    confirmed_at TIMESTAMP,
                    confirmed_by BIGINT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS content_plan (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
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
                    user_id BIGINT REFERENCES users(id),
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
            # Migration: add user_id to existing tables if column missing
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='content_plan' AND column_name='user_id'
                    ) THEN
                        ALTER TABLE content_plan ADD COLUMN user_id BIGINT REFERENCES users(id);
                    END IF;
                END $$;
            """)
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='posts' AND column_name='user_id'
                    ) THEN
                        ALTER TABLE posts ADD COLUMN user_id BIGINT REFERENCES users(id);
                    END IF;
                END $$;
            """)
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='users' AND column_name='cta'
                    ) THEN
                        ALTER TABLE users ADD COLUMN cta TEXT DEFAULT '';
                    END IF;
                END $$;
            """)
        logger.info("Database tables initialized")

    async def save_plan(self, niche: str, items: list[dict],
                        user_id: Optional[int] = None) -> list[int]:
        ids = []
        async with self._pool.acquire() as conn:
            for item in items:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO content_plan
                        (user_id, niche, topic, format, description, scheduled_date, scheduled_time)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    user_id,
                    niche,
                    item["topic"],
                    item["format"],
                    item["description"],
                    item["scheduled_date"],
                    item["scheduled_time"],
                )
                ids.append(row_id)
        logger.info("Saved %d plan items for user_id=%s", len(ids), user_id)
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

    async def save_post(self, plan_id: int, text: str, image_prompt: str,
                        user_id: Optional[int] = None) -> int:
        async with self._pool.acquire() as conn:
            post_id = await conn.fetchval(
                """
                INSERT INTO posts (plan_id, text, image_prompt, status, user_id)
                VALUES ($1, $2, $3, 'draft', $4)
                RETURNING id
                """,
                plan_id, text, image_prompt, user_id,
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

    async def create_user(
        self, user_id: int, username: Optional[str], full_name: Optional[str],
        niche: str, channel_id: int, channel_title: Optional[str],
        post_frequency: int, publish_times: str, cta: str = "",
    ) -> None:
        from datetime import datetime, timedelta
        trial_ends = datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, username, full_name, niche, channel_id, channel_title,
                                   post_frequency, publish_times, trial_ends_at, cta)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (id) DO NOTHING
                """,
                user_id, username, full_name, niche, channel_id, channel_title,
                post_frequency, publish_times, trial_ends, cta,
            )

    async def get_user(self, user_id: int) -> Optional[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)

    async def update_user_status(self, user_id: int, status: str, **fields: Any) -> None:
        allowed = {"subscription_ends_at", "trial_ends_at", "channel_id",
                   "channel_title", "post_frequency", "publish_times", "niche"}
        updates: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed}
        updates["status"] = status
        keys = list(updates.keys())
        values = list(updates.values())
        # Column names come from a hardcoded allowlist — no SQL injection risk
        set_clauses = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(keys))
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE users SET {set_clauses} WHERE id = $1",
                user_id, *values,
            )

    async def get_active_users(self) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM users WHERE status IN ('trial', 'active')"
            )

    async def get_users_trial_expiring_soon(self, days: int) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM users
                WHERE status = 'trial'
                  AND trial_ends_at BETWEEN NOW() AND NOW() + ($1 || ' days')::interval
                """,
                str(days),
            )

    async def get_users_subscription_expiring_soon(self, days: int) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM users
                WHERE status = 'active'
                  AND subscription_ends_at BETWEEN NOW() AND NOW() + ($1 || ' days')::interval
                """,
                str(days),
            )

    async def get_expired_users(self) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM users
                WHERE (status = 'trial' AND trial_ends_at < NOW())
                   OR (status = 'active' AND subscription_ends_at < NOW())
                """
            )

    async def get_all_users_stats(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS cnt FROM users GROUP BY status"
            )
            post_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS cnt FROM posts GROUP BY status"
            )
        return {
            "users": {row["status"]: row["cnt"] for row in rows},
            "posts": {row["status"]: row["cnt"] for row in post_rows},
        }

    async def save_payment(self, user_id: int, check_file_id: str) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO payments (user_id, check_file_id)
                VALUES ($1, $2)
                RETURNING id
                """,
                user_id, check_file_id,
            )

    async def get_pending_payment(self, payment_id: int) -> Optional[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT p.*, u.full_name, u.username, u.niche
                FROM payments p JOIN users u ON p.user_id = u.id
                WHERE p.id = $1
                """,
                payment_id,
            )

    async def confirm_payment(self, payment_id: int, confirmed_by: int) -> Optional[int]:
        """Confirms payment and returns user_id."""
        from datetime import datetime, timedelta
        sub_ends = datetime.utcnow() + timedelta(days=30)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM payments WHERE id = $1 AND status = 'pending'", payment_id
            )
            if not row:
                return None
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE payments
                    SET status = 'confirmed', confirmed_at = NOW(), confirmed_by = $2
                    WHERE id = $1
                    """,
                    payment_id, confirmed_by,
                )
                await conn.execute(
                    """
                    UPDATE users SET status = 'active', subscription_ends_at = $2
                    WHERE id = $1
                    """,
                    row["user_id"], sub_ends,
                )
            return row["user_id"]

    async def reject_payment(self, payment_id: int) -> Optional[int]:
        """Rejects payment and returns user_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM payments WHERE id = $1 AND status = 'pending'",
                payment_id,
            )
            if not row:
                return None
            await conn.execute(
                "UPDATE payments SET status = 'rejected' WHERE id = $1", payment_id
            )
            return row["user_id"]

    async def get_next_post_for_user(self, user_id: int, status: str) -> Optional[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT p.*, cp.topic, cp.format, cp.scheduled_date, cp.scheduled_time
                FROM posts p
                LEFT JOIN content_plan cp ON p.plan_id = cp.id
                WHERE p.user_id = $1 AND p.status = $2
                ORDER BY p.id ASC
                LIMIT 1
                """,
                user_id, status,
            )

    async def get_posts_by_status_for_user(self, user_id: int, status: str) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.*, cp.topic, cp.format, cp.scheduled_date, cp.scheduled_time
                FROM posts p
                LEFT JOIN content_plan cp ON p.plan_id = cp.id
                WHERE p.user_id = $1 AND p.status = $2
                ORDER BY cp.scheduled_date, cp.scheduled_time
                """,
                user_id, status,
            )


db = Database()
