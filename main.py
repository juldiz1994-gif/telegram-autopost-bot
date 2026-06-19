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
    from services.subscription import start_subscription_service
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
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sched.stop()
        await db.close()
        await bot.session.close()
        logger.info("Бот дұрыс тоқтатылды")


async def _cleanup(bot: Bot) -> None:
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
