import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from google import genai
from google.genai import types

from config import config
from database import db
from prompts import PLAN_PROMPT

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)


def _next_weekday(target_dow: int) -> date:
    today = date.today()
    days_ahead = target_dow - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


async def generate_weekly_plan(niche: str, user_id: Optional[int] = None) -> list[dict[str, Any]]:
    prompt = PLAN_PROMPT.format(niche=niche)

    for attempt in range(1, 6):
        try:
            response = await asyncio.to_thread(
                _client.models.generate_content,
                model=config.GEMINI_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.9,
                ),
            )
            raw = response.text.strip()
            items: list[dict] = json.loads(raw)

            if not isinstance(items, list) or len(items) != 7:
                raise ValueError(f"Күтілген 7 тақырып, алынды: {len(items) if isinstance(items, list) else type(items)}")

            publish_times = config.PUBLISH_TIMES
            enriched = []
            for i, item in enumerate(items):
                dow = int(item.get("day_of_week", i % 7))
                scheduled_date = _next_weekday(dow)
                scheduled_time = publish_times[i % len(publish_times)]
                enriched.append({
                    "topic": item["topic"],
                    "format": item["format"],
                    "description": item["description"],
                    "day_of_week": dow,
                    "scheduled_date": scheduled_date,
                    "scheduled_time": scheduled_time,
                })

            plan_ids = await db.save_plan(niche, enriched, user_id=user_id)
            for i, pid in enumerate(plan_ids):
                enriched[i]["id"] = pid

            logger.info("Апталық жоспар сақталды: %d тақырып", len(enriched))
            return enriched

        except Exception as e:
            logger.warning("Жоспар генерациясы %d-ші әрекет сәтсіз: %s", attempt, e)
            if attempt == 5:
                raise RuntimeError(f"5 әрекеттен кейін де жоспар алынбады: {e}") from e
            msg = str(e).lower()
            if "503" in msg or "unavailable" in msg:
                delay = 10.0 * attempt
            elif "429" in msg or "quota" in msg:
                delay = 20.0 * attempt
            else:
                delay = float(2 ** attempt)
            logger.info("Қайта әрекет алдында %.0f сек күту...", delay)
            await asyncio.sleep(delay)

    return []


if __name__ == "__main__":
    async def _main() -> None:
        await db.connect()
        await db.init_db()
        plan = await generate_weekly_plan(config.CONTENT_NICHE)
        for item in plan:
            print(f"[{item['format']:12s}] {item['scheduled_date']} {item['scheduled_time']} — {item['topic']}")
        await db.close()

    asyncio.run(_main())
