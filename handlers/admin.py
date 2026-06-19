import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from config import config
from database import db
from services.user_scheduler import activate_user_schedule, deactivate_user_schedule

logger = logging.getLogger(__name__)
admin_router = Router()


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


def _admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Клиенттер", callback_data="admin:users"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        ],
    ])


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    stats = await db.get_all_users_stats()
    users = stats.get("users", {})
    total = sum(users.values())
    active = users.get("active", 0)
    trial = users.get("trial", 0)
    expired = users.get("expired", 0)
    blocked = users.get("blocked", 0)

    await message.answer(
        f"👑 <b>Super-Admin Панелі</b>\n\n"
        f"👥 Барлық клиенттер: {total}\n"
        f"✅ Белсенді: {active}\n"
        f"⏳ Сынақта: {trial}\n"
        f"❌ Мерзімі өткен: {expired}\n"
        f"⛔ Бұғатталған: {blocked}",
        parse_mode="HTML",
        reply_markup=_admin_main_keyboard(),
    )


@admin_router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    await callback.answer()
    stats = await db.get_all_users_stats()
    users = stats.get("users", {})
    posts = stats.get("posts", {})
    total_clients = sum(users.values())
    active_clients = users.get("active", 0)
    monthly_revenue = active_clients * 990

    await callback.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Клиенттер: {total_clients}\n"
        f"  ✅ Белсенді: {active_clients}\n"
        f"  ⏳ Сынақта: {users.get('trial', 0)}\n"
        f"  ❌ Мерзімі өткен: {users.get('expired', 0)}\n"
        f"  ⛔ Бұғатталған: {users.get('blocked', 0)}\n\n"
        f"📝 Посттар:\n"
        f"  📢 Жарияланған: {posts.get('published', 0)}\n"
        f"  ✅ Бекітілген: {posts.get('approved', 0)}\n"
        f"  ⏳ Қарауда: {posts.get('pending_review', 0)}\n\n"
        f"💰 Болжамды табыс: ~{monthly_revenue} тг/ай",
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery) -> None:
    await callback.answer()
    users = await db.get_active_users()

    # Also get expired/blocked
    async with db._pool.acquire() as conn:
        all_users = await conn.fetch(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT 30"
        )

    status_icon = {
        "trial": "⏳", "active": "✅", "expired": "❌", "blocked": "⛔"
    }
    lines = [f"👥 <b>Клиенттер ({len(all_users)})</b>\n"]
    for u in all_users:
        icon = status_icon.get(u["status"], "?")
        name = u.get("full_name") or u.get("username") or str(u["id"])
        lines.append(f"{icon} {name} — {u['niche']} | /user_{u['id']}")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command(pattern=r"user_\d+"))
async def cmd_user_detail(message: Message) -> None:
    user_id = int(message.text.split("_")[1])
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Пайдаланушы табылмады.")
        return

    name = user.get("full_name") or user.get("username") or str(user["id"])
    username_str = f"@{user['username']}" if user.get("username") else "—"

    if user["status"] == "trial":
        ends = f"Сынақ: {user['trial_ends_at'].strftime('%d.%m.%Y')}"
    elif user["status"] == "active":
        ends = f"Жазылым: {user['subscription_ends_at'].strftime('%d.%m.%Y')}"
    else:
        ends = user["status"]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Белсендіру (+30 күн)", callback_data=f"admin_activate:{user_id}"),
            InlineKeyboardButton(text="⛔ Бұғаттау", callback_data=f"admin_block:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔓 Бұғаттауды алу", callback_data=f"admin_unblock:{user_id}"),
        ],
    ])

    await message.answer(
        f"👤 <b>{name}</b> ({username_str})\n"
        f"🆔 ID: {user_id}\n"
        f"📋 Ниша: {user['niche']}\n"
        f"📢 Канал: {user.get('channel_title', user['channel_id'])}\n"
        f"📅 Жиілік: күніне {user['post_frequency']} рет\n"
        f"📌 Статус: {user['status']}\n"
        f"⏳ {ends}\n"
        f"📆 Тіркелген: {user['created_at'].strftime('%d.%m.%Y')}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@admin_router.callback_query(F.data.startswith("admin_activate:"))
async def cb_admin_activate(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    sub_ends = datetime.utcnow() + timedelta(days=30)
    await db.update_user_status(user_id, "active", subscription_ends_at=sub_ends)
    await activate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Белсендірілді!")
    await callback.message.answer(f"✅ user_id={user_id} белсендірілді (+30 күн).")
    try:
        await bot.send_message(user_id, "✅ Жазылымыңыз белсендірілді! 30 күн.")
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin_block:"))
async def cb_admin_block(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    await db.update_user_status(user_id, "blocked")
    await deactivate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("⛔ Бұғатталды")
    await callback.message.answer(f"⛔ user_id={user_id} бұғатталды.")
    try:
        await bot.send_message(user_id, "⛔ Аккаунтыңыз бұғатталды. Қолдауға хабарлас.")
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin_unblock:"))
async def cb_admin_unblock(callback: CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data.split(":")[1])
    await db.update_user_status(user_id, "expired")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("🔓 Бұғаттау алынды")
    await callback.message.answer(f"🔓 user_id={user_id} бұғаттауы алынды (expired).")
    try:
        await bot.send_message(user_id, "🔓 Аккаунтыңыздан бұғаттау алынды. Жазылым үшін /pay жаз.")
    except Exception:
        pass


# Payment confirmation callbacks (from payments_router photos sent to admin)
@admin_router.callback_query(F.data.startswith("pay_confirm:"))
async def cb_pay_confirm(callback: CallbackQuery, bot: Bot) -> None:
    payment_id = int(callback.data.split(":")[1])
    user_id = await db.confirm_payment(payment_id, confirmed_by=config.TELEGRAM_ADMIN_ID)
    if not user_id:
        await callback.answer("❌ Төлем табылмады", show_alert=True)
        return
    await activate_user_schedule(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Расталды!")
    await callback.message.answer(f"✅ Төлем расталды. user_id={user_id} белсендірілді (30 күн).")
    try:
        await bot.send_message(
            user_id,
            "✅ <b>Төлеміңіз расталды!</b>\n\n"
            "30 күн белсендірілді. Посттар жаңадан жарияланады!",
            parse_mode="HTML",
        )
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("pay_reject:"))
async def cb_pay_reject(callback: CallbackQuery, bot: Bot) -> None:
    payment_id = int(callback.data.split(":")[1])
    user_id = await db.reject_payment(payment_id)
    if not user_id:
        await callback.answer("❌ Төлем табылмады", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("❌ Қабылданбады")
    await callback.message.answer(f"❌ Төлем қабылданбады. user_id={user_id}.")
    try:
        await bot.send_message(
            user_id,
            "❌ Чекті растай алмадық.\n\n"
            f"Kaspi нөміріне дұрыс аудардың ба? ({config.KASPI_PHONE})\n"
            "Қайтадан чек жібер немесе @support-қа хабарлас.",
        )
    except Exception:
        pass
