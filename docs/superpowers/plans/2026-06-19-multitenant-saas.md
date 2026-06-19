# Multi-Tenant SaaS Telegram Autopost Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform single-tenant autopost bot into multi-tenant SaaS with user onboarding, per-user content generation, Kaspi payment flow, and super-admin Telegram panel.

**Architecture:** Single-bot multi-tenant design. All clients share one bot process. Per-user APScheduler jobs handle posting schedules. New `handlers/` directory splits concerns: onboarding, moderation, payments, admin. Existing `database.py`, `content_planner.py`, `post_generator.py`, `publisher.py` are extended with `user_id` parameter. Super-admin is `TELEGRAM_ADMIN_ID` from env.

**Tech Stack:** Python 3.11, aiogram 3.x, google-genai SDK, asyncpg, APScheduler, Railway PostgreSQL, pytest + pytest-asyncio + pytest-mock

## Global Constraints

- All client-facing text: Kazakh (қазақ тілі). Admin-facing text: Kazakh or Russian — keep consistent with existing code (Kazakh preferred)
- Image is mandatory for every post — no post preview sent without image_path
- Gemini model IDs always from `config`, never hardcoded
- PostgreSQL placeholders: `$1, $2, ...` (asyncpg style) — never f-string SQL with values
- User status lifecycle: `trial` → `active` → `expired` | `blocked`
- Payment status: `pending` → `confirmed` | `rejected`
- `TELEGRAM_ADMIN_ID` is the sole super-admin — all admin handlers check `user.id == config.TELEGRAM_ADMIN_ID`
- Free trial = `config.TRIAL_DAYS` days (default 5)
- Subscription period = 30 days after payment confirmed
- All timestamps stored UTC in DB
- `content_plan.user_id` and `posts.user_id` added as nullable columns (existing rows stay NULL — backward compat)

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `handlers/__init__.py` | Empty package marker |
| `handlers/onboarding.py` | Registration FSM + `my_chat_member` channel auto-detect |
| `handlers/moderation.py` | Per-client post approve / reject / redo / edit callbacks |
| `handlers/admin.py` | Super-admin commands + payment confirm/reject callbacks |
| `handlers/payments.py` | Client submits Kaspi check photo |
| `services/__init__.py` | Empty package marker |
| `services/subscription.py` | Trial expiry scheduler job: remind → expire → block |
| `services/user_scheduler.py` | Add/remove per-user APScheduler publish jobs |
| `tests/__init__.py` | Empty package marker |
| `tests/test_database.py` | DB methods unit tests (mocked pool) |
| `tests/test_subscription.py` | Subscription status logic unit tests |

### Modified files
| File | What changes |
|------|-------------|
| `database.py` | `users` + `payments` tables; `user_id` on existing tables; 10 new query methods |
| `config.py` | Add `KASPI_PHONE`, `TRIAL_DAYS` |
| `content_planner.py` | `generate_weekly_plan(niche, user_id)` — saves `user_id` to plan rows |
| `post_generator.py` | `generate_post_and_save(plan_item, user_id)` — saves `user_id` to post rows |
| `publisher.py` | `publish_post(bot, post_id, channel_id)` — channel from caller, not config |
| `scheduler.py` | Full rewrite: async `start()`, per-user jobs, `add_user_jobs()` / `remove_user_jobs()` |
| `moderator_bot.py` | Remove all old single-tenant handlers; call `setup_dispatcher()` to wire new routers |
| `main.py` | `await scheduler.start()` (now async); start subscription service |
| `requirements.txt` | Add `pytest`, `pytest-asyncio`, `pytest-mock` |

---

## Task 1: Dependencies + Config

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py`
- Create: `tests/__init__.py`, `handlers/__init__.py`, `services/__init__.py`

**Interfaces:**
- Produces:
  - `config.KASPI_PHONE: str`
  - `config.TRIAL_DAYS: int`

- [ ] **Step 1: Add test dependencies to requirements.txt**

Replace the last line and add:
```
aiogram==3.13.0
google-genai==1.16.0
asyncpg==0.30.0
apscheduler==3.10.4
python-dotenv==1.0.1
aiohttp==3.10.11
aiofiles==23.2.1
pytz==2024.2
pytest==8.3.4
pytest-asyncio==0.24.0
pytest-mock==3.14.0
```

- [ ] **Step 2: Add KASPI_PHONE and TRIAL_DAYS to config.py**

In `config.py`, inside `__init__`, after `self.CONTENT_NICHE`:
```python
self.KASPI_PHONE: str = self._require("KASPI_PHONE")
self.TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "5"))
```

- [ ] **Step 3: Create package init files**

Create empty `handlers/__init__.py`, `services/__init__.py`, `tests/__init__.py`.

- [ ] **Step 4: Add KASPI_PHONE to .env.example**

Append to `.env.example`:
```
KASPI_PHONE=+77001234567
TRIAL_DAYS=5
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.py .env.example handlers/__init__.py services/__init__.py tests/__init__.py
git commit -m "feat: add KASPI_PHONE, TRIAL_DAYS config + test/handler packages"
```

---

## Task 2: Database Schema Migration

**Files:**
- Modify: `database.py`
- Create: `tests/test_database.py`

**Interfaces:**
- Consumes: `config.TRIAL_DAYS`
- Produces:
  - `db.create_user(user_id, username, full_name, niche, channel_id, channel_title, post_frequency, publish_times) -> None`
  - `db.get_user(user_id: int) -> Optional[Record]`
  - `db.get_active_users() -> list[Record]``  — status IN ('trial', 'active')
  - `db.update_user_status(user_id, status, **fields) -> None`
  - `db.get_all_users_stats() -> dict`
  - `db.save_payment(user_id: int, check_file_id: str) -> int`
  - `db.confirm_payment(payment_id: int, confirmed_by: int) -> None`
  - `db.reject_payment(payment_id: int) -> None`
  - `db.get_pending_payment(payment_id: int) -> Optional[Record]`
  - `db.get_next_post_for_user(user_id: int, status: str) -> Optional[Record]`
  - `db.get_posts_by_status_for_user(user_id: int, status: str) -> list[Record]`
  - `db.save_post(plan_id, text, image_prompt, user_id) -> int`  — adds user_id param
  - `db.get_users_trial_expiring_soon(days: int) -> list[Record]`  — for reminders

- [ ] **Step 1: Write failing tests**

Create `tests/test_database.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_create_user_inserts_row():
    """create_user executes INSERT with correct params."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    await db.create_user(
        user_id=123456,
        username="testuser",
        full_name="Test User",
        niche="Психология",
        channel_id=-1001234567890,
        channel_title="Psych Blog",
        post_frequency=2,
        publish_times="10:00,18:00",
    )
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0]
    assert "INSERT INTO users" in call_args[0]
    assert 123456 in call_args


@pytest.mark.asyncio
async def test_get_user_returns_record():
    """get_user fetches row by Telegram ID."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": 123456, "niche": "Психология", "status": "trial"}
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    result = await db.get_user(123456)
    assert result["niche"] == "Психология"
    mock_conn.fetchrow.assert_called_once_with("SELECT * FROM users WHERE id = $1", 123456)


@pytest.mark.asyncio
async def test_save_payment_returns_id():
    """save_payment inserts row and returns generated id."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = 42
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    payment_id = await db.save_payment(user_id=123456, check_file_id="AgACAgI...")
    assert payment_id == 42
    mock_conn.fetchval.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_database.py -v
```
Expected: FAIL — `create_user`, `get_user`, `save_payment` not defined yet.

- [ ] **Step 3: Add new tables to init_db in database.py**

Replace `init_db` method body with the full version (keep existing CREATE TABLE statements, add new ones after):

```python
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
    logger.info("Database tables initialized")
```

- [ ] **Step 4: Add new methods to Database class**

Add after existing methods in `database.py`:

```python
async def create_user(
    self, user_id: int, username: Optional[str], full_name: Optional[str],
    niche: str, channel_id: int, channel_title: Optional[str],
    post_frequency: int, publish_times: str,
) -> None:
    from datetime import datetime, timedelta
    trial_ends = datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, username, full_name, niche, channel_id, channel_title,
                               post_frequency, publish_times, trial_ends_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id, username, full_name, niche, channel_id, channel_title,
            post_frequency, publish_times, trial_ends,
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
            "SELECT user_id FROM payments WHERE id = $1", payment_id
        )
        if not row:
            return None
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
            "SELECT user_id FROM payments WHERE id = $1", payment_id
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
```

Also update the existing `save_post` method to accept `user_id`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_database.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add database.py tests/test_database.py
git commit -m "feat: add users/payments tables and per-user DB query methods"
```

---

## Task 3: Scheduler Rewrite (Per-User Jobs)

**Files:**
- Modify: `scheduler.py`
- Create: `services/user_scheduler.py`

**Interfaces:**
- Consumes:
  - `db.get_active_users() -> list[Record]`
  - `db.get_next_post_for_user(user_id, status) -> Optional[Record]`
  - `publisher.publish_post(bot, post_id, channel_id) -> bool`
- Produces:
  - `ContentScheduler.start() -> None` (now async coroutine)
  - `ContentScheduler.add_user_jobs(user: Record) -> None`
  - `ContentScheduler.remove_user_jobs(user_id: int) -> None`
  - `scheduler` global instance accessible as `from scheduler import scheduler`

- [ ] **Step 1: Rewrite scheduler.py**

```python
import logging
from typing import Optional

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
from database import db
from publisher import publish_post

logger = logging.getLogger(__name__)


class ContentScheduler:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def start(self) -> None:
        self._scheduler.start()
        users = await db.get_active_users()
        for user in users:
            self.add_user_jobs(dict(user))
        logger.info("Scheduler запущен, %d пайдаланушы жүктелді", len(users))

    def add_user_jobs(self, user: dict) -> None:
        user_id = user["id"]
        times_str: str = user.get("publish_times") or "10:00,18:00"
        tz = pytz.timezone(config.TIMEZONE)
        for t in times_str.split(","):
            t = t.strip()
            hour, minute = t.split(":")
            job_id = f"publish_{user_id}_{t.replace(':', '')}"
            self._scheduler.add_job(
                self._publish_for_user,
                CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
                id=job_id,
                args=[user_id],
                replace_existing=True,
            )
        logger.info("Jobs добавлены для user_id=%d", user_id)

    def remove_user_jobs(self, user_id: int) -> None:
        removed = 0
        for job in self._scheduler.get_jobs():
            if job.id.startswith(f"publish_{user_id}_"):
                job.remove()
                removed += 1
        logger.info("Jobs удалены для user_id=%d (%d шт)", user_id, removed)

    async def _publish_for_user(self, user_id: int) -> None:
        user = await db.get_user(user_id)
        if not user or user["status"] not in ("trial", "active"):
            self.remove_user_jobs(user_id)
            return

        post = await db.get_next_post_for_user(user_id, "approved")
        if post:
            success = await publish_post(self._bot, post["id"], user["channel_id"])
            if success:
                logger.info("Scheduler: пост id=%d, user_id=%d жарияланды", post["id"], user_id)
            else:
                logger.error("Scheduler: пост id=%d жариялау сәтсіз", post["id"])
        else:
            logger.info("Scheduler: user_id=%d үшін бекітілген пост жоқ", user_id)
            try:
                await self._bot.send_message(
                    user_id,
                    "⏰ Жариялау уақыты келді, бірақ бекітілген пост жоқ.\n"
                    "Жаңа пост жасалуда...",
                )
            except Exception as e:
                logger.error("user_id=%d хабарлама жіберу сәтсіз: %s", user_id, e)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler тоқтатылды")


scheduler: Optional[ContentScheduler] = None


def get_scheduler() -> ContentScheduler:
    if scheduler is None:
        raise RuntimeError("Scheduler инициализацияланбаған")
    return scheduler
```

- [ ] **Step 2: Create services/user_scheduler.py (convenience wrappers)**

```python
from scheduler import get_scheduler
from database import db


async def activate_user_schedule(user_id: int) -> None:
    """Called when user registers or payment confirmed."""
    user = await db.get_user(user_id)
    if user:
        get_scheduler().add_user_jobs(dict(user))


async def deactivate_user_schedule(user_id: int) -> None:
    """Called when user expires or is blocked."""
    get_scheduler().remove_user_jobs(user_id)
```

- [ ] **Step 3: Update publisher.py to accept channel_id parameter**

In `publisher.py`, change the function signature:

```python
async def publish_post(bot: Bot, post_id: int, channel_id: int) -> bool:
```

Remove the line `channel_id = config.TELEGRAM_CHANNEL_ID` from inside the function (it's now a parameter).

- [ ] **Step 4: Update main.py**

Replace the scheduler setup in `main.py`:

```python
import scheduler as scheduler_module

async def main() -> None:
    await db.connect()
    await db.init_db()

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    setup_dispatcher(dp)

    sched = ContentScheduler(bot)
    scheduler_module.scheduler = sched
    await sched.start()

    logger.info("Бот іске қосылды")

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        logger.info("Тоқтату сигналы алынды")
        sched.stop()
        loop.create_task(_cleanup(bot))
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sched.stop()
        await db.close()
        await bot.session.close()
        logger.info("Бот дұрыс тоқтатылды")
```

- [ ] **Step 5: Commit**

```bash
git add scheduler.py services/user_scheduler.py publisher.py main.py
git commit -m "feat: rewrite scheduler for per-user jobs, publisher accepts channel_id"
```

---

## Task 4: Onboarding FSM

**Files:**
- Create: `handlers/onboarding.py`

**Interfaces:**
- Consumes:
  - `db.get_user(user_id) -> Optional[Record]`
  - `db.create_user(...) -> None`
  - `config.KASPI_PHONE`
  - `config.TRIAL_DAYS`
  - `services.user_scheduler.activate_user_schedule(user_id)`
  - `content_planner.generate_weekly_plan(niche, user_id)` (Task 5)
- Produces:
  - `onboarding_router: Router` — register in `moderator_bot.setup_dispatcher()`
  - FSM states:
    - `OnboardingState.waiting_niche`
    - `OnboardingState.waiting_channel` — user must add bot as admin, then bot detects via `my_chat_member`
    - `OnboardingState.waiting_channel_confirm`
    - `OnboardingState.waiting_frequency`

- [ ] **Step 1: Create handlers/onboarding.py**

```python
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config
from database import db

logger = logging.getLogger(__name__)
onboarding_router = Router()


class OnboardingState(StatesGroup):
    waiting_niche = State()
    waiting_channel = State()
    waiting_channel_confirm = State()
    waiting_frequency = State()


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 рет", callback_data="freq:1"),
        InlineKeyboardButton(text="2 рет", callback_data="freq:2"),
    ]])


def _confirm_channel_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Иә, осы", callback_data=f"chan_confirm:{channel_id}"),
        InlineKeyboardButton(text="❌ Жоқ, басқасы", callback_data="chan_retry"),
    ]])


@onboarding_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if user:
        status_labels = {
            "trial": f"⏳ Сынақ мерзімі: {user['trial_ends_at'].strftime('%d.%m.%Y')}-ге дейін",
            "active": f"✅ Белсенді: {user['subscription_ends_at'].strftime('%d.%m.%Y')}-ге дейін",
            "expired": "❌ Мерзімі өткен. Жалғастыру үшін чек жібер.",
            "blocked": "⛔ Бұғатталған. Қолдауға хабарлас.",
        }
        await message.answer(
            f"👋 Қайта келдің!\n\n"
            f"📋 Ниша: {user['niche']}\n"
            f"{status_labels.get(user['status'], user['status'])}\n\n"
            f"📬 /queue — посттар кезегі\n"
            f"📊 /my_stats — статистика"
        )
        return

    await state.set_state(OnboardingState.waiting_niche)
    await message.answer(
        "👋 Сәлем! Бұл бот сенің Telegram каналыңа күнделікті пост жазып береді.\n\n"
        f"🎁 Алғашқы <b>{config.TRIAL_DAYS} күн тегін!</b>\n\n"
        "Бастайық!\n\n"
        "📝 <b>Каналыңның тақырыбы не?</b>\n"
        "Мысалы: Психология, Фитнес, Бизнес, Тамақ рецепттері...",
        parse_mode="HTML",
    )


@onboarding_router.message(OnboardingState.waiting_niche)
async def process_niche(message: Message, state: FSMContext) -> None:
    niche = (message.text or "").strip()
    if len(niche) < 2:
        await message.answer("❌ Тым қысқа. Нишаны толығырақ жаз.")
        return
    await state.update_data(niche=niche)
    await state.set_state(OnboardingState.waiting_channel)
    bot_me = await message.bot.get_me()
    await message.answer(
        f"✅ Ниша сақталды: <b>{niche}</b>\n\n"
        f"Енді <b>@{bot_me.username}</b>-ді каналыңа немесе тобыңа "
        f"<b>АДМИН</b> ретінде қос.\n\n"
        f"Қосқаннан кейін мен автоматты табамын! 🔍",
        parse_mode="HTML",
    )


@onboarding_router.my_chat_member(F.new_chat_member.status.in_({"administrator"}))
async def on_bot_added_as_admin(event: ChatMemberUpdated, state: FSMContext, bot: Bot) -> None:
    user_id = event.from_user.id
    # Only handle if this user is in waiting_channel state
    user_state = await state.get_state()
    if user_state != OnboardingState.waiting_channel.state:
        return

    channel_id = event.chat.id
    channel_title = event.chat.title or str(channel_id)

    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(OnboardingState.waiting_channel_confirm)

    await bot.send_message(
        user_id,
        f"✅ Таптым!\n\n"
        f"📢 <b>{channel_title}</b>\n\n"
        f"Осы канал ма?",
        parse_mode="HTML",
        reply_markup=_confirm_channel_keyboard(channel_id),
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_channel_confirm,
    F.data.startswith("chan_confirm:")
)
async def cb_channel_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(OnboardingState.waiting_frequency)
    await callback.message.answer(
        "🕐 <b>Күніне неше рет пост жарияланатын?</b>",
        parse_mode="HTML",
        reply_markup=_frequency_keyboard(),
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_channel_confirm,
    F.data == "chan_retry"
)
async def cb_channel_retry(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(OnboardingState.waiting_channel)
    bot_me = await callback.message.bot.get_me()
    await callback.message.answer(
        f"Жарайды, @{bot_me.username}-ді басқа каналыңа/тобыңа АДМИН ретінде қос."
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_frequency,
    F.data.startswith("freq:")
)
async def cb_frequency_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    freq = int(callback.data.split(":")[1])
    data = await state.get_data()
    await state.clear()

    publish_times = "10:00,18:00" if freq == 2 else "10:00"

    user = callback.from_user
    await db.create_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        niche=data["niche"],
        channel_id=data["channel_id"],
        channel_title=data["channel_title"],
        post_frequency=freq,
        publish_times=publish_times,
    )

    from datetime import datetime, timedelta
    trial_end = datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"🎉 <b>Тіркеу аяқталды!</b>\n\n"
        f"📋 Ниша: {data['niche']}\n"
        f"📢 Канал: {data['channel_title']}\n"
        f"📅 Постар: күніне {freq} рет ({publish_times})\n"
        f"⏳ Тегін мерзім: {trial_end.strftime('%d.%m.%Y')}-ге дейін\n\n"
        f"⏳ Контент-жоспар жасалуда...",
        parse_mode="HTML",
    )

    # Trigger content generation in background
    asyncio.create_task(_bootstrap_user(callback.message.bot, user.id, data["niche"]))


async def _bootstrap_user(bot: Bot, user_id: int, niche: str) -> None:
    """Generate first weekly plan + posts after registration."""
    try:
        from content_planner import generate_weekly_plan
        from services.user_scheduler import activate_user_schedule

        plan = await generate_weekly_plan(niche, user_id)
        await activate_user_schedule(user_id)

        await bot.send_message(
            user_id,
            f"✅ Апталық жоспар дайын! {len(plan)} тақырып жасалды.\n"
            f"Посттар генерацияланудад, жақында аласың...",
        )

        # Generate posts for all plan items
        from post_generator import generate_post_and_save
        from image_generator import generate_image

        for item in plan:
            try:
                post_data = await generate_post_and_save(item, user_id)
                await bot.send_message(user_id, "⏳ Пост жасалды, сурет генерацияланудад...")
                await generate_image(post_data["image_prompt"], post_data["id"])
                # Send for moderation
                from handlers.moderation import send_post_preview_to_user
                await send_post_preview_to_user(bot, post_data["id"], user_id)
            except Exception as e:
                logger.error("Bootstrap post error user_id=%d: %s", user_id, e)

    except Exception as e:
        logger.error("Bootstrap error user_id=%d: %s", user_id, e)
        await bot.send_message(user_id, f"❌ Контент жасауда қате: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add handlers/onboarding.py
git commit -m "feat: add onboarding FSM with my_chat_member channel auto-detection"
```

---

## Task 5: Per-User Content Generation

**Files:**
- Modify: `content_planner.py`
- Modify: `post_generator.py`

**Interfaces:**
- Consumes: `db.save_plan(niche, items, user_id)` — existing method updated to accept user_id
- Produces:
  - `generate_weekly_plan(niche: str, user_id: int) -> list[dict]`
  - `generate_post_and_save(plan_item: dict, user_id: int) -> dict`

- [ ] **Step 1: Update database.py save_plan to accept user_id**

In `database.py`, update `save_plan` method:

```python
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
```

- [ ] **Step 2: Update content_planner.py**

Change function signature and the `save_plan` call:

```python
async def generate_weekly_plan(niche: str, user_id: Optional[int] = None) -> list[dict]:
```

Change the `save_plan` call inside the function:
```python
plan_ids = await db.save_plan(niche, enriched, user_id=user_id)
```

- [ ] **Step 3: Update post_generator.py**

Change `generate_post_and_save` signature:

```python
async def generate_post_and_save(plan_item: dict,
                                  user_id: Optional[int] = None) -> dict[str, Any]:
    result = await generate_post(
        topic=plan_item["topic"],
        format_type=plan_item["format"],
        niche=plan_item.get("niche") or config.CONTENT_NICHE,
    )
    post_id = await db.save_post(
        plan_id=plan_item["id"],
        text=result["text"],
        image_prompt=result["image_prompt"],
        user_id=user_id,
    )
    return {"id": post_id, "text": result["text"], "image_prompt": result["image_prompt"]}
```

- [ ] **Step 4: Commit**

```bash
git add content_planner.py post_generator.py database.py
git commit -m "feat: add user_id to content generation pipeline"
```

---

## Task 6: Per-User Moderation

**Files:**
- Create: `handlers/moderation.py`

**Interfaces:**
- Consumes:
  - `db.get_post_by_id(post_id) -> Optional[Record]`
  - `db.update_post_status(post_id, status, **fields) -> None`
  - `db.get_posts_by_status_for_user(user_id, status) -> list[Record]`
  - `post_generator.generate_post(topic, format_type, niche) -> dict`
  - `image_generator.generate_image(prompt, post_id) -> Optional[str]`
- Produces:
  - `moderation_router: Router`
  - `send_post_preview_to_user(bot, post_id, user_id) -> None` — called from onboarding bootstrap

- [ ] **Step 1: Create handlers/moderation.py**

```python
import asyncio
import json
import logging
import os
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from google import genai
from google.genai import types

from config import config
from database import db

logger = logging.getLogger(__name__)
moderation_router = Router()
CAPTION_LIMIT = 1024


class EditState(StatesGroup):
    waiting_for_edit = State()


def _moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Бекіту", callback_data=f"u_approve:{post_id}"),
        InlineKeyboardButton(text="🔄 Қайта жаз", callback_data=f"u_redo:{post_id}"),
        InlineKeyboardButton(text="✏️ Өңдеу", callback_data=f"u_edit:{post_id}"),
        InlineKeyboardButton(text="❌ Өткізіп жібер", callback_data=f"u_reject:{post_id}"),
    ]])


async def send_post_preview_to_user(bot: Bot, post_id: int, user_id: int) -> None:
    post = await db.get_post_by_id(post_id)
    if not post:
        return

    meta = (
        f"\n\n📋 <b>Формат:</b> {post.get('format', '—')}\n"
        f"📌 <b>Тақырып:</b> {post.get('topic', '—')}\n"
        f"📅 <b>Жоспарланған:</b> {post.get('scheduled_date', '—')} {post.get('scheduled_time', '—')}"
    )
    keyboard = _moderation_keyboard(post_id)
    text: str = post["text"]
    image_path: str | None = post["image_path"]

    try:
        if image_path and os.path.exists(image_path):
            photo = FSInputFile(image_path)
            caption = (text + meta) if len(text + meta) <= CAPTION_LIMIT else meta
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            if len(text + meta) > CAPTION_LIMIT:
                await bot.send_message(user_id, text, reply_markup=keyboard)
        else:
            await bot.send_message(
                user_id,
                text + meta,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except TelegramBadRequest as e:
        logger.error("Post %d preview send failed to user %d: %s", post_id, user_id, e)

    await db.update_post_status(post_id, "pending_review")


@moderation_router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    approved = await db.get_posts_by_status_for_user(user_id, "approved")
    pending = await db.get_posts_by_status_for_user(user_id, "pending_review")
    lines = ["📬 <b>Посттар кезегі</b>\n"]
    if approved:
        lines.append(f"✅ <b>Бекітілгендер ({len(approved)}):</b>")
        for p in approved:
            lines.append(f"  [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if pending:
        lines.append(f"\n⏳ <b>Қарауда ({len(pending)}):</b>")
        for p in pending:
            lines.append(f"  [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if not approved and not pending:
        lines.append("Кезек бос.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@moderation_router.message(Command("my_stats"))
async def cmd_my_stats(message: Message) -> None:
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    from database import db as _db
    async with _db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS cnt FROM posts WHERE user_id=$1 GROUP BY status",
            user_id
        )
    stats = {r["status"]: r["cnt"] for r in rows}
    labels = {
        "draft": "📝 Жобалар", "pending_review": "⏳ Қарауда",
        "approved": "✅ Бекітілгендер", "published": "📢 Жарияланғандар",
        "rejected": "❌ Өткізілгендер",
    }
    lines = [f"📊 <b>Менің посттарым</b>\n"]
    for key, label in labels.items():
        lines.append(f"{label}: {stats.get(key, 0)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@moderation_router.callback_query(F.data.startswith("u_approve:"))
async def cb_approve(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await db.update_post_status(post_id, "approved")
    scheduled = f"{post.get('scheduled_date', '?')} {post.get('scheduled_time', '?')}"
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Бекітілді!")
    await callback.message.answer(f"✅ Пост бекітілді. Жоспарланған: {scheduled}")


@moderation_router.callback_query(F.data.startswith("u_reject:"))
async def cb_reject(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await db.update_post_status(post_id, "rejected")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("❌ Өткізілді")
    await callback.message.answer("❌ Пост өткізілді, жарияланбайды.")


@moderation_router.callback_query(F.data.startswith("u_redo:"))
async def cb_redo(callback: CallbackQuery, bot: Bot) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await callback.answer("🔄 Қайта жасалуда...")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    try:
        from post_generator import generate_post
        from image_generator import generate_image
        user = await db.get_user(callback.from_user.id)
        result = await generate_post(
            topic=post.get("topic", ""),
            format_type=post.get("format", "tips"),
            niche=user["niche"] if user else config.CONTENT_NICHE,
        )
        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE posts SET text=$1, image_prompt=$2, image_path=NULL, status='draft' WHERE id=$3",
                result["text"], result["image_prompt"], post_id,
            )
        await send_post_preview_to_user(bot, post_id, callback.from_user.id)
        asyncio.create_task(generate_image(result["image_prompt"], post_id))
    except Exception as e:
        logger.error("Redo error post %d: %s", post_id, e)
        await bot.send_message(callback.from_user.id, f"❌ Қайта жазу қатесі: {e}")


@moderation_router.callback_query(F.data.startswith("u_edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await state.set_state(EditState.waiting_for_edit)
    await state.update_data(post_id=post_id)
    await callback.answer()
    await callback.message.answer(
        f"✏️ Нені өзгерту керек? Жаз (AI өзгертеді):"
    )


@moderation_router.message(EditState.waiting_for_edit)
async def process_edit(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    post_id: int = data["post_id"]
    edits: str = message.text or ""
    await state.clear()

    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != message.from_user.id:
        await message.answer("❌ Пост табылмады.")
        return

    await message.answer("⏳ AI мәтінді өзгертуде...")
    from prompts import SYSTEM_PROMPT
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    edit_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Ағымдағы пост:\n{post['text']}\n\n"
        f"Өзгерістер:\n{edits}\n\n"
        f"Постты өзгерістерді ескере отырып қайта жаз. "
        f'JSON: {{"text": "...", "image_prompt": "..."}}'
    )
    try:
        import asyncio as _asyncio
        response = await _asyncio.to_thread(
            client.models.generate_content,
            model=config.GEMINI_TEXT_MODEL,
            contents=edit_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        result = json.loads(response.text)
        new_text: str = result["text"]
        new_image_prompt: str = result.get("image_prompt", post["image_prompt"] or "")

        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE posts SET text=$1, image_prompt=$2, image_path=NULL, status='draft' WHERE id=$3",
                new_text, new_image_prompt, post_id,
            )
        from image_generator import generate_image
        await send_post_preview_to_user(bot, post_id, message.from_user.id)
        asyncio.create_task(generate_image(new_image_prompt, post_id))
    except Exception as e:
        logger.error("Edit error post %d: %s", post_id, e)
        await message.answer(f"❌ Өңдеу қатесі: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add handlers/moderation.py
git commit -m "feat: per-user post moderation handler"
```

---

## Task 7: Payment Flow

**Files:**
- Create: `handlers/payments.py`

**Interfaces:**
- Consumes:
  - `db.get_user(user_id) -> Optional[Record]`
  - `db.save_payment(user_id, check_file_id) -> int`
  - `config.KASPI_PHONE`
  - `config.TELEGRAM_ADMIN_ID`
- Produces:
  - `payments_router: Router`

- [ ] **Step 1: Create handlers/payments.py**

```python
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, PhotoSize

from config import config
from database import db

logger = logging.getLogger(__name__)
payments_router = Router()


@payments_router.message(Command("pay"))
async def cmd_pay(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    if user["status"] == "active":
        await message.answer(
            f"✅ Жазылымың белсенді: {user['subscription_ends_at'].strftime('%d.%m.%Y')}-ге дейін."
        )
        return
    await message.answer(
        f"💳 <b>Жазылым төлемі</b>\n\n"
        f"Сома: <b>990 тг/ай</b>\n"
        f"Kaspi: <code>{config.KASPI_PHONE}</code>\n\n"
        f"Аударғаннан кейін осы чатқа <b>чек суретін жібер</b> — "
        f"30 минут ішінде растаймыз.",
        parse_mode="HTML",
    )


@payments_router.message(F.photo)
async def handle_check_photo(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        return  # Not registered, ignore photo

    if user["status"] == "active":
        await message.answer("✅ Жазылымың белсенді, төлем қажет емес.")
        return

    # Take highest resolution photo
    photo: PhotoSize = message.photo[-1]
    file_id = photo.file_id

    payment_id = await db.save_payment(user_id=user["id"], check_file_id=file_id)

    await message.answer(
        "✅ Чек алынды! Жақын арада растаймыз (30 мин ішінде)."
    )

    # Notify admin
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Растау", callback_data=f"pay_confirm:{payment_id}"
        ),
        InlineKeyboardButton(
            text="❌ Қабылдамау", callback_data=f"pay_reject:{payment_id}"
        ),
    ]])

    name = user.get("full_name") or user.get("username") or str(user["id"])
    username_str = f"@{user['username']}" if user.get("username") else "username жоқ"

    await message.bot.send_photo(
        chat_id=config.TELEGRAM_ADMIN_ID,
        photo=file_id,
        caption=(
            f"💳 <b>Жаңа төлем!</b>\n\n"
            f"👤 {name} ({username_str})\n"
            f"📋 Ниша: {user['niche']}\n"
            f"📅 Тіркелген: {user['created_at'].strftime('%d.%m.%Y')}\n"
            f"🆔 Төлем ID: {payment_id}"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    logger.info("Payment id=%d from user_id=%d sent to admin", payment_id, user["id"])
```

- [ ] **Step 2: Commit**

```bash
git add handlers/payments.py
git commit -m "feat: client payment check submission handler"
```

---

## Task 8: Super-Admin Panel

**Files:**
- Create: `handlers/admin.py`

**Interfaces:**
- Consumes:
  - `db.get_all_users_stats() -> dict`
  - `db.get_active_users() -> list[Record]`
  - `db.get_user(user_id) -> Optional[Record]`
  - `db.update_user_status(user_id, status) -> None`
  - `db.confirm_payment(payment_id, confirmed_by) -> Optional[int]`
  - `db.reject_payment(payment_id) -> Optional[int]`
  - `services.user_scheduler.activate_user_schedule(user_id)`
  - `services.user_scheduler.deactivate_user_schedule(user_id)`
- Produces:
  - `admin_router: Router`
  - Middleware: `AdminOnlyMiddleware` — blocks non-admin on admin router

- [ ] **Step 1: Create handlers/admin.py**

```python
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from config import config
from database import db
from services.user_scheduler import activate_user_schedule, deactivate_user_schedule

logger = logging.getLogger(__name__)
admin_router = Router()


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Any, event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and user.id != config.TELEGRAM_ADMIN_ID:
            if isinstance(event, Message):
                await event.answer("⛔ Қатынас жоқ.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Қатынас жоқ.", show_alert=True)
            return
        return await handler(event, data)


def _admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Клиенттер", callback_data="admin:users"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        ],
    ])


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    stats = await db.get_all_users_stats()
    users = stats.get("users", {})
    total = sum(users.values())
    active = users.get("active", 0)
    trial = users.get("trial", 0)
    expired = users.get("expired", 0)
    blocked = users.get("blocked", 0)

    await message.answer(
        f"👑 <b>Super-Admin Панелі</b>\n\n"
        f"👥 Барлық клиенттер: {total}\n"
        f"✅ Белсенді: {active}\n"
        f"⏳ Сынақта: {trial}\n"
        f"❌ Мерзімі өткен: {expired}\n"
        f"⛔ Бұғатталған: {blocked}",
        parse_mode="HTML",
        reply_markup=_admin_main_keyboard(),
    )


@admin_router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    await callback.answer()
    stats = await db.get_all_users_stats()
    users = stats.get("users", {})
    posts = stats.get("posts", {})
    total_clients = sum(users.values())
    active_clients = users.get("active", 0)
    monthly_revenue = active_clients * 990

    await callback.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Клиенттер: {total_clients}\n"
        f"  ✅ Белсенді: {active_clients}\n"
        f"  ⏳ Сынақта: {users.get('trial', 0)}\n"
        f"  ❌ Мерзімі өткен: {users.get('expired', 0)}\n"
        f"  ⛔ Бұғатталған: {users.get('blocked', 0)}\n\n"
        f"📝 Посттар:\n"
        f"  📢 Жарияланған: {posts.get('published', 0)}\n"
        f"  ✅ Бекітілген: {posts.get('approved', 0)}\n"
        f"  ⏳ Қарауда: {posts.get('pending_review', 0)}\n\n"
        f"💰 Болжамды табыс: ~{monthly_revenue} тг/ай",
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    await callback.answer()
    users = await db.get_active_users()

    # Also get expired/blocked
    async with db._pool.acquire() as conn:
        all_users = await conn.fetch(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT 30"
        )

    status_icon = {
        "trial": "⏳", "active": "✅", "expired": "❌", "blocked": "⛔"
    }
    lines = [f"👥 <b>Клиенттер ({len(all_users)})</b>\n"]
    for u in all_users:
        icon = status_icon.get(u["status"], "?")
        name = u.get("full_name") or u.get("username") or str(u["id"])
        lines.append(f"{icon} {name} — {u['niche']} | /user_{u['id']}")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command(pattern=r"user_\d+"))
async def cmd_user_detail(message: Message) -> None:
    user_id = int(message.text.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Пайдаланушы табылмады.")
        return

    name = user.get("full_name") or user.get("username") or str(user["id"])
    username_str = f"@{user['username']}" if user.get("username") else "—"

    if user["status"] == "trial":
        ends = f"Сынақ: {user['trial_ends_at'].strftime('%d.%m.%Y')}"
    elif user["status"] == "active":
        ends = f"Жазылым: {user['subscription_ends_at'].strftime('%d.%m.%Y')}"
    else:
        ends = user["status"]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Белсендіру (+30 күн)", callback_data=f"admin_activate:{user_id}"),
            InlineKeyboardButton(text="⛔ Бұғаттау", callback_data=f"admin_block:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔓 Бұғаттауды алу", callback_data=f"admin_unblock:{user_id}"),
        ],
    ])

    await message.answer(
        f"👤 <b>{name}</b> ({username_str})\n"
        f"🆔 ID: {user_id}\n"
        f"📋 Ниша: {user['niche']}\n"
        f"📢 Канал: {user.get('channel_title', user['channel_id'])}\n"
        f"📅 Жиілік: күніне {user['post_frequency']} рет\n"
        f"📌 Статус: {user['status']}\n"
        f"⏳ {ends}\n"
        f"📆 Тіркелген: {user['created_at'].strftime('%d.%m.%Y')}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@admin_router.callback_query(F.data.startswith("admin_activate:"))
async def cb_admin_activate(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    sub_ends = datetime.utcnow() + timedelta(days=30)
    await db.update_user_status(user_id, "active", subscription_ends_at=sub_ends)
    await activate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Белсендірілді!")
    await callback.message.answer(f"✅ user_id={user_id} белсендірілді (+30 күн).")
    try:
        await bot.send_message(user_id, "✅ Жазылымыңыз белсендірілді! 30 күн.")
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin_block:"))
async def cb_admin_block(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    await db.update_user_status(user_id, "blocked")
    await deactivate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("⛔ Бұғатталды")
    await callback.message.answer(f"⛔ user_id={user_id} бұғатталды.")
    try:
        await bot.send_message(user_id, "⛔ Аккаунтыңыз бұғатталды. Қолдауға хабарлас.")
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin_unblock:"))
async def cb_admin_unblock(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    await db.update_user_status(user_id, "expired")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("🔓 Бұғаттау алынды")
    await callback.message.answer(f"🔓 user_id={user_id} бұғаттауы алынды (expired).")
    try:
        await bot.send_message(user_id, "🔓 Аккаунтыңыздан бұғаттау алынды. Жазылым үшін /pay жаз.")
    except Exception:
        pass


# Payment confirmation callbacks (from payments_router photos sent to admin)
@admin_router.callback_query(F.data.startswith("pay_confirm:"))
async def cb_pay_confirm(callback: CallbackQuery, bot: Bot) -> None:
    payment_id = int(callback.data.split(":")[1])
    user_id = await db.confirm_payment(payment_id, confirmed_by=config.TELEGRAM_ADMIN_ID)
    if not user_id:
        await callback.answer("❌ Төлем табылмады", show_alert=True)
        return
    await activate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Расталды!")
    await callback.message.answer(f"✅ Төлем расталды. user_id={user_id} белсендірілді (30 күн).")
    try:
        await bot.send_message(
            user_id,
            "✅ <b>Төлеміңіз расталды!</b>\n\n"
            "30 күн белсендірілді. Посттар жаңадан жарияланады!",
            parse_mode="HTML",
        )
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("pay_reject:"))
async def cb_pay_reject(callback: CallbackQuery, bot: Bot) -> None:
    payment_id = int(callback.data.split(":")[1])
    user_id = await db.reject_payment(payment_id)
    if not user_id:
        await callback.answer("❌ Төлем табылмады", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("❌ Қабылданбады")
    await callback.message.answer(f"❌ Төлем қабылданбады. user_id={user_id}.")
    try:
        await bot.send_message(
            user_id,
            "❌ Чекті растай алмадық.\n\n"
            f"Kaspi нөміріне дұрыс аудардың ба? ({config.KASPI_PHONE})\n"
            "Қайтадан чек жібер немесе @support-қа хабарлас.",
        )
    except Exception:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add handlers/admin.py
git commit -m "feat: super-admin panel with payment confirm/reject callbacks"
```

---

## Task 9: Subscription Service

**Files:**
- Create: `services/subscription.py`

**Interfaces:**
- Consumes:
  - `db.get_users_trial_expiring_soon(days) -> list[Record]`
  - `db.get_users_subscription_expiring_soon(days) -> list[Record]`
  - `db.get_expired_users() -> list[Record]`
  - `db.update_user_status(user_id, status) -> None`
  - `services.user_scheduler.deactivate_user_schedule(user_id)`
  - `config.KASPI_PHONE`
- Produces:
  - `start_subscription_service(bot, scheduler_instance) -> None` — registers daily APScheduler jobs

- [ ] **Step 1: Create services/subscription.py**

```python
import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
from database import db
from services.user_scheduler import deactivate_user_schedule

logger = logging.getLogger(__name__)


async def _send_trial_reminders(bot: Bot) -> None:
    """Send reminder 3 days before trial ends."""
    users = await db.get_users_trial_expiring_soon(days=3)
    for user in users:
        try:
            ends = user["trial_ends_at"].strftime("%d.%m.%Y")
            await bot.send_message(
                user["id"],
                f"⏰ <b>Тегін мерзімің {ends}-де аяқталады!</b>\n\n"
                f"Жалғастыру үшін 990 тг аудар:\n"
                f"📱 Kaspi: <code>{config.KASPI_PHONE}</code>\n\n"
                f"Аударғаннан кейін чек жіберсең — 30 күн қосылады. /pay",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Trial reminder failed for user_id=%d: %s", user["id"], e)


async def _send_subscription_reminders(bot: Bot) -> None:
    """Send reminder 3 days before subscription ends."""
    users = await db.get_users_subscription_expiring_soon(days=3)
    for user in users:
        try:
            ends = user["subscription_ends_at"].strftime("%d.%m.%Y")
            await bot.send_message(
                user["id"],
                f"⏰ <b>Жазылымың {ends}-де аяқталады!</b>\n\n"
                f"Жалғастыру үшін 990 тг аудар:\n"
                f"📱 Kaspi: <code>{config.KASPI_PHONE}</code>\n\n"
                f"Чекті жіберсең — тоқтаусыз жалғасады. /pay",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Sub reminder failed for user_id=%d: %s", user["id"], e)


async def _expire_overdue_users(bot: Bot) -> None:
    """Block users whose trial/subscription has expired."""
    users = await db.get_expired_users()
    for user in users:
        try:
            await db.update_user_status(user["id"], "expired")
            await deactivate_user_schedule(user["id"])
            await bot.send_message(
                user["id"],
                "❌ <b>Мерзімің аяқталды.</b>\n\n"
                "Посттар тоқтатылды. Жалғастыру үшін /pay жаз.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Expire user_id=%d failed: %s", user["id"], e)


def start_subscription_service(bot: Bot, apscheduler: AsyncIOScheduler) -> None:
    """Register daily subscription jobs on the shared scheduler."""
    import pytz
    tz = pytz.timezone(config.TIMEZONE)

    apscheduler.add_job(
        _send_trial_reminders,
        CronTrigger(hour=9, minute=0, timezone=tz),
        id="trial_reminders",
        args=[bot],
        replace_existing=True,
    )
    apscheduler.add_job(
        _send_subscription_reminders,
        CronTrigger(hour=9, minute=5, timezone=tz),
        id="sub_reminders",
        args=[bot],
        replace_existing=True,
    )
    apscheduler.add_job(
        _expire_overdue_users,
        CronTrigger(hour=0, minute=30, timezone=tz),
        id="expire_users",
        args=[bot],
        replace_existing=True,
    )
    logger.info("Subscription service jobs registered")
```

- [ ] **Step 2: Add subscription service startup to main.py**

In `main.py`, after `await sched.start()`:

```python
from services.subscription import start_subscription_service
start_subscription_service(bot, sched._scheduler)
```

- [ ] **Step 3: Commit**

```bash
git add services/subscription.py main.py
git commit -m "feat: subscription service — reminders + auto-expiry"
```

---

## Task 10: Wire Everything Together

**Files:**
- Modify: `moderator_bot.py`
- Modify: `main.py` (final version)

**Interfaces:**
- Consumes: all routers from `handlers/`
- Produces: `setup_dispatcher(dp)` that registers all routers with correct middleware

- [ ] **Step 1: Rewrite moderator_bot.py**

```python
from aiogram import Dispatcher

from handlers.admin import AdminOnlyMiddleware, admin_router
from handlers.moderation import moderation_router
from handlers.onboarding import onboarding_router
from handlers.payments import payments_router


def setup_dispatcher(dp: Dispatcher) -> None:
    # Admin router: protected by AdminOnlyMiddleware
    admin_router.message.middleware(AdminOnlyMiddleware())
    admin_router.callback_query.middleware(AdminOnlyMiddleware())

    # Order matters: onboarding first (handles /start and FSM),
    # then payments (photo handler), then moderation (callbacks),
    # then admin (protected commands)
    dp.include_router(onboarding_router)
    dp.include_router(payments_router)
    dp.include_router(moderation_router)
    dp.include_router(admin_router)
```

- [ ] **Step 2: Final main.py**

```python
import asyncio
import logging
import signal

import scheduler as scheduler_module
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import db
from moderator_bot import setup_dispatcher
from scheduler import ContentScheduler
from services.subscription import start_subscription_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await db.connect()
    await db.init_db()

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    setup_dispatcher(dp)

    sched = ContentScheduler(bot)
    scheduler_module.scheduler = sched
    await sched.start()

    start_subscription_service(bot, sched._scheduler)

    logger.info("Бот іске қосылды")

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        logger.info("Тоқтату сигналы алынды")
        sched.stop()
        loop.create_task(_cleanup(bot))
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types() + ["my_chat_member"],
        )
    finally:
        sched.stop()
        await db.close()
        await bot.session.close()
        logger.info("Бот дұрыс тоқтатылды")


async def _cleanup(bot: Bot) -> None:
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Note:** `allowed_updates` must include `"my_chat_member"` so the bot receives events when added as admin to a channel.

- [ ] **Step 3: Add KASPI_PHONE to Railway environment variables**

In Railway dashboard → project → Variables:
```
KASPI_PHONE=+77001234567
TRIAL_DAYS=5
```

- [ ] **Step 4: Run tests one final time**

```bash
pytest tests/ -v
```
Expected: all tests PASS.

- [ ] **Step 5: Push to GitHub and verify Railway deployment**

```bash
git add moderator_bot.py main.py
git commit -m "feat: wire all handlers, enable my_chat_member updates"
git push origin master
```

Wait ~2 minutes for Railway rebuild. Check Railway logs for:
```
Database tables initialized
Scheduler запущен, 0 пайдаланушы жүктелді
Бот іске қосылды
```

- [ ] **Step 6: End-to-end test via Telegram**

1. Send `/start` to bot → should see niche prompt
2. Type "Психология" → should see "add bot as admin" instruction
3. Add bot as admin to a test channel → bot should auto-detect and ask to confirm
4. Confirm channel → choose frequency → see "Тіркеу аяқталды!"
5. Wait for posts to appear in your DM for moderation
6. Test ✅ Approve → post should appear in the channel
7. Send a test check photo → admin should receive notification with ✅/❌ buttons
8. Confirm payment → user receives "Төлем расталды!" message
9. Send `/admin` as admin → see dashboard with stats

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| 5-day free trial | Task 2 (`create_user` sets `trial_ends_at`) |
| User enters niche | Task 4 (`OnboardingState.waiting_niche`) |
| Channel auto-detect via `my_chat_member` | Task 4 (`on_bot_added_as_admin`) |
| Post frequency choice (1 or 2) | Task 4 (`cb_frequency_chosen`) |
| Per-user content generation | Task 5 (`generate_weekly_plan(niche, user_id)`) |
| Mandatory photo on posts | Task 6 (`send_post_preview_to_user` — sends image if exists; image retry in `image_generator.py` already handles retries) |
| Per-user moderation (approve/reject/edit/redo) | Task 6 |
| Kaspi payment + check photo | Task 7 |
| Admin confirm/reject with buttons (no ID typing) | Task 8 (`pay_confirm:`, `pay_reject:` callbacks) |
| Super-admin panel in Telegram | Task 8 |
| Trial expiry reminder (3 days) | Task 9 |
| Auto-expiry when period ends | Task 9 |
| 30-day subscription after payment | Task 8 (`confirm_payment` sets `subscription_ends_at = +30 days`) |
| Per-user APScheduler jobs | Task 3 |
| Scheduler loads active users on restart | Task 3 (`start()` calls `get_active_users()`) |

### Gap identified and fixed

**Gap:** `send_post_preview_to_user` in Task 6 is called from `_bootstrap_user` in Task 4 — this is a forward reference. Since both are in the same Python process it works (import at call time), but to be safe, `_bootstrap_user` should import `send_post_preview_to_user` inside the function body (already done in Task 4 code: `from handlers.moderation import send_post_preview_to_user`).

**Gap:** `my_chat_member` updates need explicit inclusion in `allowed_updates`. Fixed in Task 10 Step 2 main.py.

**Gap:** Old single-tenant commands (`/plan`, `/generate_all`, etc.) from original `moderator_bot.py` — removed in Task 10 Step 1. If needed for admin debugging, they can be added to `handlers/admin.py` later.
