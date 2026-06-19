import asyncio
import json
import logging
import os
from typing import Any

from aiogram import Bot, F, Router
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
)
from google import genai
from google.genai import types

from config import config
from database import db

logger = logging.getLogger(__name__)
moderation_router = Router()
CAPTION_LIMIT = 1024


class EditState(StatesGroup):
    waiting_for_edit = State()


def _moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Бекіту", callback_data=f"u_approve:{post_id}"),
        InlineKeyboardButton(text="🔄 Қайта жаз", callback_data=f"u_redo:{post_id}"),
        InlineKeyboardButton(text="✏️ Өңдеу", callback_data=f"u_edit:{post_id}"),
        InlineKeyboardButton(text="❌ Өткізіп жібер", callback_data=f"u_reject:{post_id}"),
    ]])


async def send_post_preview_to_user(bot: Bot, post_id: int, user_id: int) -> None:
    post = await db.get_post_by_id(post_id)
    if not post:
        return

    meta = (
        f"\n\n📋 <b>Формат:</b> {post.get('format', '—')}\n"
        f"📌 <b>Тақырып:</b> {post.get('topic', '—')}\n"
        f"📅 <b>Жоспарланған:</b> {post.get('scheduled_date', '—')} {post.get('scheduled_time', '—')}"
    )
    keyboard = _moderation_keyboard(post_id)
    text: str = post["text"]
    image_path: str | None = post["image_path"]

    try:
        if image_path and os.path.exists(image_path):
            photo = FSInputFile(image_path)
            caption = (text + meta) if len(text + meta) <= CAPTION_LIMIT else meta
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            if len(text + meta) > CAPTION_LIMIT:
                await bot.send_message(user_id, text)
        else:
            await bot.send_message(
                user_id,
                text + meta,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        await db.update_post_status(post_id, "pending_review")
    except TelegramBadRequest as e:
        logger.error("Post %d preview send failed to user %d: %s", post_id, user_id, e)


@moderation_router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    approved = await db.get_posts_by_status_for_user(user_id, "approved")
    pending = await db.get_posts_by_status_for_user(user_id, "pending_review")
    lines = ["📬 <b>Посттар кезегі</b>\n"]
    if approved:
        lines.append(f"✅ <b>Бекітілгендер ({len(approved)}):</b>")
        for p in approved:
            lines.append(f"  [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if pending:
        lines.append(f"\n⏳ <b>Қарауда ({len(pending)}):</b>")
        for p in pending:
            lines.append(f"  [{p.get('format', '?')}] {str(p.get('topic', '?'))[:50]}")
    if not approved and not pending:
        lines.append("Кезек бос.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@moderation_router.message(Command("my_stats"))
async def cmd_my_stats(message: Message) -> None:
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS cnt FROM posts WHERE user_id=$1 GROUP BY status",
            user_id
        )
    stats = {r["status"]: r["cnt"] for r in rows}
    labels = {
        "draft": "📝 Жобалар", "pending_review": "⏳ Қарауда",
        "approved": "✅ Бекітілгендер", "published": "📢 Жарияланғандар",
        "rejected": "❌ Өткізілгендер",
    }
    lines = [f"📊 <b>Менің посттарым</b>\n"]
    for key, label in labels.items():
        lines.append(f"{label}: {stats.get(key, 0)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@moderation_router.callback_query(F.data.startswith("u_approve:"))
async def cb_approve(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await db.update_post_status(post_id, "approved")
    scheduled = f"{post.get('scheduled_date', '?')} {post.get('scheduled_time', '?')}"
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Бекітілді!")
    await callback.message.answer(f"✅ Пост бекітілді. Жоспарланған: {scheduled}")


@moderation_router.callback_query(F.data.startswith("u_reject:"))
async def cb_reject(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await db.update_post_status(post_id, "rejected")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("❌ Өткізілді")
    await callback.message.answer("❌ Пост өткізілді, жарияланбайды.")


@moderation_router.callback_query(F.data.startswith("u_redo:"))
async def cb_redo(callback: CallbackQuery, bot: Bot) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await callback.answer("🔄 Қайта жасалуда...")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    try:
        from post_generator import generate_post
        from image_generator import generate_image
        user = await db.get_user(callback.from_user.id)
        result = await generate_post(
            topic=post.get("topic", ""),
            format_type=post.get("format", "tips"),
            niche=user["niche"] if user else config.CONTENT_NICHE,
        )
        async with db._pool.acquire() as conn:
            await conn.execute(
                "UPDATE posts SET text=$1, image_prompt=$2, image_path=NULL, status='draft' WHERE id=$3",
                result["text"], result["image_prompt"], post_id,
            )
        await send_post_preview_to_user(bot, post_id, callback.from_user.id)
        asyncio.create_task(generate_image(result["image_prompt"], post_id))
    except Exception as e:
        logger.error("Redo error post %d: %s", post_id, e)
        await bot.send_message(callback.from_user.id, f"❌ Қайта жазу қатесі: {e}")


@moderation_router.callback_query(F.data.startswith("u_edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != callback.from_user.id:
        await callback.answer("❌ Қатынас жоқ", show_alert=True)
        return
    await state.set_state(EditState.waiting_for_edit)
    await state.update_data(post_id=post_id)
    await callback.answer()
    await callback.message.answer(
        f"✏️ Нені өзгерту керек? Жаз (AI өзгертеді):"
    )


@moderation_router.message(EditState.waiting_for_edit)
async def process_edit(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    post_id: int = data["post_id"]
    edits: str = message.text or ""
    await state.clear()

    post = await db.get_post_by_id(post_id)
    if not post or post.get("user_id") != message.from_user.id:
        await message.answer("❌ Пост табылмады.")
        return

    await message.answer("⏳ AI мәтінді өзгертуде...")
    from prompts import SYSTEM_PROMPT
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    edit_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Ағымдағы пост:\n{post['text']}\n\n"
        f"Өзгерістер:\n{edits}\n\n"
        f"Постты өзгерістерді ескере отырып қайта жаз. "
        f'JSON: {{"text": "...", "image_prompt": "..."}}'
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
                new_text, new_image_prompt, post_id,
            )
        from image_generator import generate_image
        await send_post_preview_to_user(bot, post_id, message.from_user.id)
        asyncio.create_task(generate_image(new_image_prompt, post_id))
    except Exception as e:
        logger.error("Edit error post %d: %s", post_id, e)
        await message.answer(f"❌ Өңдеу қатесі: {e}")
