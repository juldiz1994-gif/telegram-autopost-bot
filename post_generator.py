import asyncio
import json
import logging
from typing import Any, Optional

from google import genai
from google.genai import types

from config import config
from database import db
from prompts import FORMAT_PROMPTS

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)


def _retry_delay(attempt: int, error: Exception) -> float:
    msg = str(error).lower()
    if "503" in msg or "unavailable" in msg:
        return 10.0 * attempt
    if "429" in msg or "quota" in msg:
        return 20.0 * attempt
    return float(2 ** attempt)


async def generate_post(topic: str, format_type: str, niche: str, cta: str = "") -> dict[str, Any]:
    if format_type not in FORMAT_PROMPTS:
        logger.warning("Белгісіз формат '%s', 'tips' қолданылады", format_type)
        format_type = "tips"

    prompt = FORMAT_PROMPTS[format_type](topic, niche, cta)

    for attempt in range(1, 6):
        try:
            response = await asyncio.to_thread(
                _client.models.generate_content,
                model=config.GEMINI_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.85,
                ),
            )
            raw = response.text.strip()
            data: dict = json.loads(raw)
            text: str = data["text"]
            image_prompt: str = data.get("image_prompt", "")

            length = len(text)
            if length < 500 or length > 900:
                logger.warning("Пост ұзындығы %d шек [500,900]-дан тыс, %d-ші әрекет", length, attempt)
                if attempt == 5:
                    text = text[:900] if length > 900 else text
                else:
                    await asyncio.sleep(2)
                    continue

            return {"text": text, "image_prompt": image_prompt}

        except Exception as e:
            logger.warning("Пост генерациясы %d-ші әрекет сәтсіз: %s", attempt, e)
            if attempt == 5:
                raise RuntimeError(f"5 әрекеттен кейін де пост алынбады: {e}") from e
            delay = _retry_delay(attempt, e)
            logger.info("Қайта әрекет алдында %.0f сек күту...", delay)
            await asyncio.sleep(delay)

    return {}


async def generate_post_and_save(plan_item: dict,
                                  user_id: Optional[int] = None) -> dict[str, Any]:
    cta = ""
    if user_id:
        user = await db.get_user(user_id)
        if user:
            cta = user.get("cta") or ""
    result = await generate_post(
        topic=plan_item["topic"],
        format_type=plan_item["format"],
        niche=plan_item.get("niche") or config.CONTENT_NICHE,
        cta=cta,
    )
    post_id = await db.save_post(
        plan_id=plan_item["id"],
        text=result["text"],
        image_prompt=result["image_prompt"],
        user_id=user_id,
    )
    return {"id": post_id, "text": result["text"], "image_prompt": result["image_prompt"]}


async def generate_posts_for_plan(plan_id: int | None = None) -> list[dict[str, Any]]:
    plan_items = await db.get_plan(plan_id)
    results = []
    for item in plan_items:
        item_dict = dict(item)
        try:
            post = await generate_post_and_save(item_dict)
            results.append(post)
            logger.info("Пост сақталды id=%d, тақырып: %s", post["id"], item_dict["topic"])
        except Exception as e:
            logger.error("Тақырып бойынша пост генерациясы сәтсіз '%s': %s", item_dict.get("topic"), e)
        await asyncio.sleep(2)
    return results


if __name__ == "__main__":
    async def _main() -> None:
        await db.connect()
        posts = await generate_posts_for_plan()
        for p in posts:
            print(f"Пост #{p['id']}: {p['text'][:80]}...")
        await db.close()

    asyncio.run(_main())
