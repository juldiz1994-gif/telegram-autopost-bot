import logging

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

    def start(self) -> None:
        tz = pytz.timezone(config.TIMEZONE)
        for publish_time in config.PUBLISH_TIMES:
            self._scheduler.add_job(
                self.check_and_publish,
                CronTrigger(
                    hour=publish_time.hour,
                    minute=publish_time.minute,
                    timezone=tz,
                ),
                id=f"publish_{publish_time.hour:02d}{publish_time.minute:02d}",
                replace_existing=True,
            )
            logger.info(
                "Жариялау тапсырмасы жоспарланды: %02d:%02d %s",
                publish_time.hour,
                publish_time.minute,
                config.TIMEZONE,
            )
        self._scheduler.start()

    async def check_and_publish(self) -> None:
        logger.info("Scheduler: бекітілген посттарды тексеру")
        post = await db.get_next_post("approved")
        if post:
            success = await publish_post(self._bot, post["id"])
            if success:
                logger.info("Scheduler: пост id=%d жарияланды", post["id"])
            else:
                logger.error("Scheduler: пост id=%d жариялау сәтсіз", post["id"])
        else:
            logger.info("Scheduler: бекітілген пост жоқ, әкімшіге еске салу жіберілді")
            try:
                await self._bot.send_message(
                    config.TELEGRAM_ADMIN_ID,
                    "⏰ Жариялау уақыты келді! Бірақ бекітілген посттар жоқ.\n"
                    "Жаңа пост жасау үшін /generate пайдаланыңыз.",
                )
            except Exception as e:
                logger.error("Әкімшіге еске салу жіберу сәтсіз: %s", e)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler тоқтатылды")
