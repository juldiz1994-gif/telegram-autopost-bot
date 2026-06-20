import logging

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
                f"Жалғастыру үшін 1990 тг аудар:\n"
                f"📱 Kaspi: <code>{config.KASPI_PHONE}</code>\n"
                f"👤 Аты: <b>{config.KASPI_NAME}</b>\n\n"
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
                f"Жалғастыру үшін 1990 тг аудар:\n"
                f"📱 Kaspi: <code>{config.KASPI_PHONE}</code>\n"
                f"👤 Аты: <b>{config.KASPI_NAME}</b>\n\n"
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
