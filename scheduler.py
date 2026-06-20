import asyncio
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
        self._scheduler.add_job(
            self._process_pending_images,
            "interval",
            minutes=5,
            id="process_pending_images",
            replace_existing=True,
        )
        # Daily at 03:00: refill any user with fewer than 3 approved posts
        self._scheduler.add_job(
            self._check_and_refill_plans,
            CronTrigger(hour=3, minute=0, timezone=pytz.timezone(config.TIMEZONE)),
            id="check_and_refill_plans",
            replace_existing=True,
        )
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
            draft = await db.get_next_post_for_user(user_id, "draft")
            if not draft:
                logger.info("Scheduler: user_id=%d контент таусылды, жаңа жоспар жасалуда", user_id)
                asyncio.create_task(self._regenerate_plan(user_id, user["niche"]))

    async def _regenerate_plan(self, user_id: int, niche: str) -> None:
        try:
            from content_planner import generate_weekly_plan
            from post_generator import generate_post_and_save
            from image_generator import generate_image

            await self._bot.send_message(
                user_id,
                "📅 Жаңа апталық жоспар жасалуда... Посттар дайын болғанда кесте бойынша автоматты жарияланады.",
            )
            plan = await generate_weekly_plan(niche, user_id)

            for item in plan:
                try:
                    post_data = await generate_post_and_save(item, user_id)
                    await generate_image(post_data["image_prompt"], post_data["id"])
                    await db.update_post_status(post_data["id"], "approved")
                except Exception as e:
                    logger.error("Regenerate post error user_id=%d: %s", user_id, e)
        except Exception as e:
            logger.error("Regenerate plan error user_id=%d: %s", user_id, e)

    async def _process_pending_images(self) -> None:
        """Find posts without images, generate them, and auto-approve."""
        try:
            from image_generator import generate_image
            async with db._pool.acquire() as conn:
                posts = await conn.fetch(
                    """SELECT id, user_id, image_prompt FROM posts
                       WHERE status = 'draft'
                       AND (image_path IS NULL OR image_path = '')
                       LIMIT 10"""
                )
            if not posts:
                return
            logger.info("Auto-image job: %d пост өңделеді", len(posts))
            for post in posts:
                try:
                    await generate_image(post["image_prompt"], post["id"])
                    await db.update_post_status(post["id"], "approved")
                    logger.info("Auto-approved post id=%d user_id=%d", post["id"], post["user_id"])
                except Exception as e:
                    logger.error("Auto-image error post id=%d: %s", post["id"], e)
                    # Approve even without image so content doesn't get stuck
                    await db.update_post_status(post["id"], "approved")
        except Exception as e:
            logger.error("_process_pending_images error: %s", e)

    async def _check_and_refill_plans(self) -> None:
        """Daily job: users with fewer than 3 approved posts get a new weekly plan."""
        try:
            users = await db.get_active_users()
            for user in users:
                user_id = user["id"]
                try:
                    async with db._pool.acquire() as conn:
                        count = await conn.fetchval(
                            "SELECT COUNT(*) FROM posts WHERE user_id=$1 AND status='approved'",
                            user_id,
                        )
                    if count < 3:
                        logger.info("Refill: user_id=%d has %d approved posts, regenerating", user_id, count)
                        asyncio.create_task(self._regenerate_plan(user_id, user["niche"]))
                except Exception as e:
                    logger.error("Refill check error user_id=%d: %s", user_id, e)
        except Exception as e:
            logger.error("_check_and_refill_plans error: %s", e)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler тоқтатылды")


scheduler: Optional[ContentScheduler] = None


def get_scheduler() -> ContentScheduler:
    if scheduler is None:
        raise RuntimeError("Scheduler инициализацияланбаған")
    return scheduler
