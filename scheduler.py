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
            # Check if posts are already in the moderation pipeline
            pending = await db.get_next_post_for_user(user_id, "pending_review")
            draft = await db.get_next_post_for_user(user_id, "draft")
            if pending or draft:
                logger.info("Scheduler: user_id=%d постар модерацияда күтуде", user_id)
            else:
                # No content at all — generate a new weekly plan
                logger.info("Scheduler: user_id=%d контент таусылды, жаңа жоспар жасалуда", user_id)
                asyncio.create_task(self._regenerate_plan(user_id, user["niche"]))

    async def _regenerate_plan(self, user_id: int, niche: str) -> None:
        try:
            from content_planner import generate_weekly_plan
            from post_generator import generate_post_and_save
            from image_generator import generate_image

            await self._bot.send_message(user_id, "📅 Жаңа апталық жоспар жасалуда...")
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

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler тоқтатылды")


scheduler: Optional[ContentScheduler] = None


def get_scheduler() -> ContentScheduler:
    if scheduler is None:
        raise RuntimeError("Scheduler инициализацияланбаған")
    return scheduler
