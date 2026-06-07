import asyncio
import json
import logging
import os
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from google import genai
from google.genai import types

from config import config
from content_planner import generate_weekly_plan
from database import db
from image_generator import generate_image
from post_generator import generate_post, generate_post_and_save
from prompts import SYSTEM_PROMPT
from publisher import publish_post

logger = logging.getLogger(__name__)

router = Router()
CAPTION_LIMIT = 1024


# ── FSM ───────────────────────────────────────────────────────────────────────

class EditState(StatesGroup):
    waiting_for_edit = State()


# ── Middleware ────────────────────────────────────────────────────────────────

class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler: Any, event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and user.id != config.TELEGRAM_ADMIN_ID:
            if isinstance(event, Message):
                await event.answer("⛔ Қатынас жоқ.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Қатынас жоқ.", show_alert=True)
            return
        return await handler(event, data)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Бекіту", callback_data=f"approve:{post_id}"),
        InlineKeyboardButton(text="🔄 Қайта жаз", callback_data=f"redo:{post_id}"),
        InlineKeyboardButton(text="✏️ Өңдеу", callback_data=f"edit:{post_id}"),
        InlineKeyboardButton(text="❌ Қабылдамау", callback_data=f"reject:{post_id}"),
    ]])


async def _send_post_preview(bot: Bot, post_id: int) -> None:
    post = await db.get_post_by_id(post_id)
    if not post:
        await bot.send_message(config.TELEGRAM_ADMIN_ID, f"❌ Пост #{post_id} табылмады.")
        return

    meta = (
        f"\n\n📋 <b>Формат:</b> {post.get('format', '—')}\n"
        f"📌 <b>Тақырып:</b> {post.get('topic', '—')}\n"
        f"📅 <b>Жоспарланған:</b> {post.get('scheduled_date', '—')} {post.get('scheduled_time', '—')}\n"
        f"🆔 <b>Пост ID:</b> {post_id}"
    )
    keyboard = _moderation_keyboard(post_id)
    text: str = post["text"]
    image_path: str | None = post["image_path"]

    try:
        if image_path and os.path.exists(image_path):
            photo = FSInputFile(image_path)
            caption = (text + meta) if len(text + meta) <= CAPTION_LIMIT else meta
            await bot.send_photo(
                chat_id=config.TELEGRAM_ADMIN_ID,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            if len(text + meta) > CAPTION_LIMIT:
                await bot.send_message(
                    config.TELEGRAM_ADMIN_ID,
                    text,
                    reply_markup=keyboard,
                )
        else:
            await bot.send_message(
                config.TELEGRAM_ADMIN_ID,
                text + meta,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except TelegramBadRequest as e:
        logger.error("Пост %d алдын ала қарауды жіберу сәтсіз: %s", post_id, e)
        await bot.send_message(
            config.TELEGRAM_ADMIN_ID,
            f"⚠️ Пост #{post_id} алдын ала қарауда қате: {e}",
        )

    await db.update_post_status(post_id, "pending_review")


# ── Commands ──────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Telegram Autopost Bot</b>\n\n"
        "Командалар:\n"
        "/plan — апталық контент-жоспар жасау\n"
        "/show_plan — ағымдағы жоспарды көрсету\n"
        "/generate — келесі постты генерациялау\n"
        "/generate_all — жоспардың барлық посттарын генерациялау\n"
        "/queue — посттар кезегі\n"
        "/stats — статистика\n"
        "/publish_now — бекітілген келесі постты дереу жариялау",
        parse_mode="HTML",
    )


@router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    await message.answer("⏳ Апталық контент-жоспар жасалуда...")
    try:
        plan = await generate_weekly_plan(config.CONTENT_NICHE)
        lines = [f"📅 <b>Апталық контент-жоспар</b> (ниша: {config.CONTENT_NICHE})\n"]
        for item in plan:
            lines.append(
                f"• <b>{item['scheduled_date']}</b> {item['scheduled_time']} "
                f"[{item['format']}] {item['topic']}"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("Жоспар генерациясы қатесі: %s", e)
        await message.answer(f"❌ Жоспар жасау қатесі: {e}")


@router.message(Command("show_plan"))
async def cmd_show_plan(message: Message) -> None:
    plan = await db.get_plan()
    if not plan:
        await message.answer("📭 Контент-жоспар бос. Жоспар жасау үшін /plan пайдаланыңыз.")
        return
    lines = ["📅 <b>Ағымдағы контент-жоспар</b>\n"]
    for item in plan:
        lines.append(
            f"• <b>{item['scheduled_date']}</b> {item['scheduled_time']} "
            f"[{item['format']}] {item['topic']}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("generate"))
async def cmd_generate(message: Message, bot: Bot) -> None:
    await message.answer("⏳ Келесі пост генерацияланудад...")

    post_item = await db.get_next_post("draft")
    if not post_item:
        plan = await db.get_plan()
        if not plan:
            await message.answer("❌ Жоспар жоқ. Алдымен /plan пайдаланыңыз.")
            return

        existing_plan_ids: set[int] = set()
        for status in ("draft", "pending_review", "approved", "published"):
            for p in await db.get_posts_by_status(status):
                if p["plan_id"]:
                    existing_plan_ids.add(p["plan_id"])

        available = [dict(p) for p in plan if p["id"] not in existing_plan_ids]
        if not available:
            await message.answer("✅ Барлық посттар генерацияланды. /queue пайдаланыңыз.")
            return

        try:
            post_data = await generate_post_and_save(available[0])
            post_id = post_data["id"]
        except Exception as e:
            await message.answer(f"❌ Пост генерациясы қатесі: {e}")
            return
    else:
        post_id = post_item["id"]

    await message.answer("👁 Алдын ала қарау жіберілуде...")
    await _send_post_preview(bot, post_id)
    post_record = await db.get_post_by_id(post_id)
    asyncio.create_task(generate_image(post_record["image_prompt"] or "", post_id))


@router.message(Command("generate_all"))
async def cmd_generate_all(message: Message, bot: Bot) -> None:
    plan = await db.get_plan()
    if not plan:
        await message.answer("❌ Жоспар жоқ. Алдымен /plan пайдаланыңыз.")
        return

    existing_plan_ids: set[int] = set()
    for status in ("draft", "pending_review", "approved", "published"):
        for p in await db.get_posts_by_status(status):
            if p["plan_id"]:
                existing_plan_ids.add(p["plan_id"])

    available = [dict(p) for p in plan if p["id"] not in existing_plan_ids]
    if not available:
        await message.answer("✅ Барлық посттар генерацияланды.")
        return

    await message.answer(f"⏳ {len(available)} пост генерацияланудад...")
    success = 0
    for item in available:
        try:
            post_data = await generate_post_and_save(item)
            post_id = post_data["id"]
            await _send_post_preview(bot, post_id)
            post_record = await db.get_post_by_id(post_id)
            asyncio.create_task(generate_image(post_record["image_prompt"] or "", post_id))
            success += 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Тақырып бойынша пост генерациясы сәтсіз '%s': %s", item.get("topic"), e)

    await message.answer(f"✅ Дайын! Модерацияға жіберілді: {success}/{len(available)}")


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    approved = await db.get_posts_by_status("approved")
    pending = await db.get_posts_by_status("pending_review")
    lines = ["📬 <b>Посттар кезегі</b>\n"]
    if approved:
        lines.append(f"✅ <b>Бекітілгендер ({len(approved)}):</b>")
        for p in approved:
            lines.append(f"  #{p['id']} [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if pending:
        lines.append(f"\n⏳ <b>Қарауда ({len(pending)}):</b>")
        for p in pending:
            lines.append(f"  #{p['id']} [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if not approved and not pending:
        lines.append("Кезек бос.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = await db.get_stats()
    labels = {
        "draft": "📝 Жобалар",
        "pending_review": "⏳ Қарауда",
        "approved": "✅ Бекітілгендер",
        "published": "📢 Жарияланғандар",
        "rejected": "❌ Қабылданбағандар",
    }
    lines = ["📊 <b>Посттар статистикасы</b>\n"]
    for key, label in labels.items():
        lines.append(f"{label}: {stats.get(key, 0)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("publish_now"))
async def cmd_publish_now(message: Message, bot: Bot) -> None:
    post = await db.get_next_post("approved")
    if not post:
        await message.answer("❌ Жариялауға бекітілген пост жоқ.")
        return
    await message.answer(f"📤 #{post['id']} посты жарияланудад...")
    success = await publish_post(bot, post["id"])
    if success:
        await message.answer(f"✅ #{post['id']} посты жарияланды!")
    else:
        await message.answer(f"❌ #{post['id']} постын жариялау сәтсіз. Логтарды тексеріңіз.")


# ── Callback handlers ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    await db.update_post_status(post_id, "approved")
    post = await db.get_post_by_id(post_id)
    scheduled = (
        f"{post.get('scheduled_date', '?')} {post.get('scheduled_time', '?')}"
        if post
        else "?"
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Бекітілді!")
    await callback.message.answer(
        f"✅ #{post_id} посты бекітілді. Жоспарланған: {scheduled}"
    )


@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    await db.update_post_status(post_id, "rejected")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("❌ Қабылданбады")
    await callback.message.answer(f"❌ #{post_id} посты қабылданбады.")


@router.callback_query(F.data.startswith("redo:"))
async def cb_redo(callback: CallbackQuery, bot: Bot) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post:
        await callback.answer("❌ Пост табылмады")
        return

    await callback.answer("🔄 Қайта генерацияланудад...")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    try:
        result = await generate_post(
            topic=post.get("topic", ""),
            format_type=post.get("format", "tips"),
            niche=config.CONTENT_NICHE,
        )
        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE posts SET text=$1, image_prompt=$2, image_path=NULL, status='draft' WHERE id=$3",
                result["text"],
                result["image_prompt"],
                post_id,
            )
        await _send_post_preview(bot, post_id)
        asyncio.create_task(generate_image(result["image_prompt"], post_id))
    except Exception as e:
        logger.error("#{} постын қайта жазу сәтсіз: %s", post_id, e)
        await bot.send_message(
            config.TELEGRAM_ADMIN_ID,
            f"❌ #{post_id} постын қайта жазу қатесі: {e}",
        )


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    await state.set_state(EditState.waiting_for_edit)
    await state.update_data(post_id=post_id)
    await callback.answer()
    await callback.message.answer(
        f"✏️ #{post_id} посты. AI-ға өзгерістерді жазыңыз (нені өзгерту, қосу немесе алып тастау керек):"
    )


@router.message(EditState.waiting_for_edit)
async def process_edit(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    post_id: int = data["post_id"]
    edits: str = message.text or ""
    await state.clear()

    post = await db.get_post_by_id(post_id)
    if not post:
        await message.answer(f"❌ #{post_id} посты табылмады.")
        return

    await message.answer("⏳ AI мәтінді түзетуде...")

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    edit_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Ағымдағы пост:\n{post['text']}\n\n"
        f"Автордың өзгерістері:\n{edits}\n\n"
        f"Постты өзгерістерді ескере отырып қайта жаз. "
        f'JSON қайтар: {{"text": "...", "image_prompt": "..."}}'
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=config.GEMINI_TEXT_MODEL,
            contents=edit_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        result = json.loads(response.text)
        new_text: str = result["text"]
        new_image_prompt: str = result.get("image_prompt", post["image_prompt"] or "")

        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE posts SET text=$1, image_prompt=$2, image_path=NULL, status='draft' WHERE id=$3",
                new_text,
                new_image_prompt,
                post_id,
            )

        await _send_post_preview(bot, post_id)
        asyncio.create_task(generate_image(new_image_prompt, post_id))

    except Exception as e:
        logger.error("#{} постын өңдеу сәтсіз: %s", post_id, e)
        await message.answer(f"❌ Өңдеу қатесі: {e}")


# ── Registration ──────────────────────────────────────────────────────────────

def setup_dispatcher(dp: Dispatcher) -> None:
    dp.message.middleware(AdminOnlyMiddleware())
    dp.callback_query.middleware(AdminOnlyMiddleware())
    dp.include_router(router)
