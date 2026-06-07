import asyncio
import logging
import os
from typing import Optional

import aiofiles
from google import genai
from google.genai import types

from config import config
from database import db
from prompts import IMAGE_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)
IMAGES_DIR = "images"


async def generate_image(prompt: str, post_id: int) -> Optional[str]:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    full_prompt = IMAGE_PROMPT_TEMPLATE.format(prompt=prompt)

    for attempt in range(1, 5):
        try:
            response = await asyncio.to_thread(
                _client.models.generate_content,
                model=config.GEMINI_IMAGE_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )

            image_data: Optional[bytes] = None
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    image_data = part.inline_data.data
                    break

            if not image_data:
                logger.warning("Сурет деректері жоқ post_id=%d, %d-ші әрекет", post_id, attempt)
                if attempt < 4:
                    await asyncio.sleep(5)
                    continue
                return None

            file_path = os.path.join(IMAGES_DIR, f"{post_id}.png")
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(image_data)

            await db.update_post_status(post_id, "draft", image_path=file_path)
            logger.info("Сурет сақталды: %s", file_path)
            return file_path

        except Exception as e:
            msg = str(e).lower()
            logger.warning("Сурет генерациясы %d-ші әрекет сәтсіз post_id=%d: %s", attempt, post_id, e)
            if "limit: 0" in msg:
                logger.warning("Сурет квотасы бітті (limit:0), retry болмайды post_id=%d", post_id)
                return None
            if attempt < 4:
                delay = 15.0 * attempt if ("503" in msg or "unavailable" in msg or "429" in msg) else 5.0
                logger.info("Сурет retry алдында %.0f сек күту...", delay)
                await asyncio.sleep(delay)

    logger.error("Сурет генерациясы 4 әрекеттен кейін де сәтсіз post_id=%d", post_id)
    return None


async def generate_images_for_posts(status: str = "draft") -> int:
    posts = await db.get_posts_by_status(status)
    no_image_posts = [p for p in posts if not p["image_path"]]
    count = 0
    for post in no_image_posts:
        image_path = await generate_image(post["image_prompt"] or "", post["id"])
        if image_path:
            count += 1
        await asyncio.sleep(3)
    logger.info("%d посттан %d сурет генерацияланды", len(no_image_posts), count)
    return count


if __name__ == "__main__":
    async def _main() -> None:
        await db.connect()
        n = await generate_images_for_posts()
        print(f"Генерацияланды: {n} сурет")
        await db.close()

    asyncio.run(_main())
