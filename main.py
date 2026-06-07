import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import db
from moderator_bot import setup_dispatcher
from scheduler import ContentScheduler

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

    scheduler = ContentScheduler(bot)
    scheduler.start()

    times_str = ", ".join(f"{t.hour:02d}:{t.minute:02d}" for t in config.PUBLISH_TIMES)
    logger.info("Бот іске қосылды. Scheduler белсенді. Жариялау кестесі: %s %s", times_str, config.TIMEZONE)

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        logger.info("Тоқтату сигналы алынды")
        scheduler.stop()
        loop.create_task(_cleanup(bot))
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows SIGTERM қолдамайды

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.stop()
        await db.close()
        await bot.session.close()
        logger.info("Бот дұрыс тоқтатылды")


async def _cleanup(bot: Bot) -> None:
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
