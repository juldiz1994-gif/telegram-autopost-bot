import logging
import os
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import FSInputFile

from config import config
from database import db

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024


async def publish_post(bot: Bot, post_id: int) -> bool:
    post = await db.get_post_by_id(post_id)
    if not post:
        logger.error("Пост id=%d табылмады", post_id)
        return False

    if post["status"] != "approved":
        logger.warning("Пост id=%d статусы '%s', 'approved' емес", post_id, post["status"])
        return False

    text: str = post["text"]
    image_path: str | None = post["image_path"]
    channel_id = config.TELEGRAM_CHANNEL_ID

    try:
        if image_path and os.path.exists(image_path):
            photo = FSInputFile(image_path)
            if len(text) <= CAPTION_LIMIT:
                msg = await bot.send_photo(chat_id=channel_id, photo=photo, caption=text)
            else:
                msg = await bot.send_photo(chat_id=channel_id, photo=photo)
                await bot.send_message(chat_id=channel_id, text=text)
        else:
            msg = await bot.send_message(chat_id=channel_id, text=text)

        await db.update_post_status(
            post_id,
            "published",
            message_id=msg.message_id,
            published_at=datetime.now(timezone.utc),
        )
        logger.info("Пост id=%d арнаға жарияланды: %s", post_id, channel_id)
        return True

    except Exception as e:
        logger.error("Пост id=%d жариялау сәтсіз: %s", post_id, e)
        return False
