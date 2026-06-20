import logging
import os
import random
from typing import Optional
from urllib.parse import quote

import aiofiles
import aiohttp

from database import db

logger = logging.getLogger(__name__)

IMAGES_DIR = "images"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"


async def generate_image(prompt: str, post_id: int) -> Optional[str]:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    seed = random.randint(0, 999999)
    url = POLLINATIONS_URL.format(prompt=quote(prompt), seed=seed)

    for attempt in range(1, 4):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200:
                        logger.warning("Pollinations қате %d post_id=%d, %d-ші әрекет", resp.status, post_id, attempt)
                        if attempt < 3:
                            import asyncio
                            await asyncio.sleep(10 * attempt)
                            continue
                        return None

                    image_data = await resp.read()

            file_path = os.path.join(IMAGES_DIR, f"{post_id}.png")
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(image_data)

            await db.update_post_status(post_id, "draft", image_path=file_path)
            logger.info("Сурет сақталды: %s (Pollinations)", file_path)
            return file_path

        except Exception as e:
            logger.warning("Сурет генерациясы %d-ші әрекет сәтсіз post_id=%d: %s", attempt, post_id, e)
            if attempt < 3:
                import asyncio
                await asyncio.sleep(10 * attempt)

    logger.error("Сурет генерациясы 3 әрекеттен кейін де сәтсіз post_id=%d", post_id)
    return None


async def generate_images_for_posts(status: str = "draft") -> int:
    import asyncio
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
    import asyncio

    async def _main() -> None:
        await db.connect()
        n = await generate_images_for_posts()
        print(f"Генерацияланды: {n} сурет")
        await db.close()

    asyncio.run(_main())
