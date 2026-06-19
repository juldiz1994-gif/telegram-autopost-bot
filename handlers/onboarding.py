import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config
from database import db

logger = logging.getLogger(__name__)
onboarding_router = Router()


class OnboardingState(StatesGroup):
    waiting_niche = State()
    waiting_channel = State()
    waiting_channel_confirm = State()
    waiting_frequency = State()


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 рет", callback_data="freq:1"),
        InlineKeyboardButton(text="2 рет", callback_data="freq:2"),
    ]])


def _confirm_channel_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Иә, осы", callback_data=f"chan_confirm:{channel_id}"),
        InlineKeyboardButton(text="❌ Жоқ, басқасы", callback_data="chan_retry"),
    ]])


@onboarding_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if user:
        status_labels = {
            "trial": f"⏳ Сынақ мерзімі: {user['trial_ends_at'].strftime('%d.%m.%Y')}-ге дейін",
            "active": f"✅ Белсенді: {user['subscription_ends_at'].strftime('%d.%m.%Y')}-ге дейін",
            "expired": "❌ Мерзімі өткен. Жалғастыру үшін чек жібер.",
            "blocked": "⛔ Бұғатталған. Қолдауға хабарлас.",
        }
        await message.answer(
            f"👋 Қайта келдің!\n\n"
            f"📋 Ниша: {user['niche']}\n"
            f"{status_labels.get(user['status'], user['status'])}\n\n"
            f"📬 /queue — посттар кезегі\n"
            f"📊 /my_stats — статистика"
        )
        return

    await state.set_state(OnboardingState.waiting_niche)
    await message.answer(
        "👋 Сәлем! Бұл бот сенің Telegram каналыңа күнделікті пост жазып береді.\n\n"
        f"🎁 Алғашқы <b>{config.TRIAL_DAYS} күн тегін!</b>\n\n"
        "Бастайық!\n\n"
        "📝 <b>Каналыңның тақырыбы не?</b>\n"
        "Мысалы: Психология, Фитнес, Бизнес, Тамақ рецепттері...",
        parse_mode="HTML",
    )


@onboarding_router.message(OnboardingState.waiting_niche)
async def process_niche(message: Message, state: FSMContext) -> None:
    niche = (message.text or "").strip()
    if len(niche) < 2:
        await message.answer("❌ Тым қысқа. Нишаны толығырақ жаз.")
        return
    await state.update_data(niche=niche)
    await state.set_state(OnboardingState.waiting_channel)
    bot_me = await message.bot.get_me()
    await message.answer(
        f"✅ Ниша сақталды: <b>{niche}</b>\n\n"
        f"Енді <b>@{bot_me.username}</b>-ді каналыңа немесе тобыңа "
        f"<b>АДМИН</b> ретінде қос.\n\n"
        f"Қосқаннан кейін мен автоматты табамын! 🔍",
        parse_mode="HTML",
    )


@onboarding_router.my_chat_member(F.new_chat_member.status.in_({"administrator"}))
async def on_bot_added_as_admin(event: ChatMemberUpdated, bot: Bot, fsm_storage: BaseStorage) -> None:
    if not event.from_user:
        return
    user_id = event.from_user.id

    # FSM state is stored under the private-chat key (chat_id == user_id)
    private_key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    current_state = await fsm_storage.get_state(private_key)
    if current_state != OnboardingState.waiting_channel.state:
        return

    channel_id = event.chat.id
    channel_title = event.chat.title or str(channel_id)

    await fsm_storage.update_data(private_key, {"channel_id": channel_id, "channel_title": channel_title})
    await fsm_storage.set_state(private_key, OnboardingState.waiting_channel_confirm.state)

    await bot.send_message(
        user_id,
        f"✅ Таптым!\n\n"
        f"📢 <b>{channel_title}</b>\n\n"
        f"Осы канал ма?",
        parse_mode="HTML",
        reply_markup=_confirm_channel_keyboard(channel_id),
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_channel_confirm,
    F.data.startswith("chan_confirm:")
)
async def cb_channel_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(OnboardingState.waiting_frequency)
    await callback.message.answer(
        "🕐 <b>Күніне неше рет пост жарияланатын?</b>",
        parse_mode="HTML",
        reply_markup=_frequency_keyboard(),
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_channel_confirm,
    F.data == "chan_retry"
)
async def cb_channel_retry(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(OnboardingState.waiting_channel)
    bot_me = await callback.message.bot.get_me()
    await callback.message.answer(
        f"Жарайды, @{bot_me.username}-ді басқа каналыңа/тобыңа АДМИН ретінде қос."
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_frequency,
    F.data.startswith("freq:")
)
async def cb_frequency_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    freq = int(callback.data.split(":")[1])
    data = await state.get_data()

    publish_times = "10:00,18:00" if freq == 2 else "10:00"

    user = callback.from_user
    await db.create_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        niche=data["niche"],
        channel_id=data["channel_id"],
        channel_title=data["channel_title"],
        post_frequency=freq,
        publish_times=publish_times,
    )
    await state.clear()

    from datetime import datetime, timedelta
    trial_end = datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"🎉 <b>Тіркеу аяқталды!</b>\n\n"
        f"📋 Ниша: {data['niche']}\n"
        f"📢 Канал: {data['channel_title']}\n"
        f"📅 Постар: күніне {freq} рет ({publish_times})\n"
        f"⏳ Тегін мерзім: {trial_end.strftime('%d.%m.%Y')}-ге дейін\n\n"
        f"⏳ Контент-жоспар жасалуда...",
        parse_mode="HTML",
    )

    # Trigger content generation in background
    asyncio.create_task(_bootstrap_user(callback.message.bot, user.id, data["niche"]))


async def _bootstrap_user(bot: Bot, user_id: int, niche: str) -> None:
    """Generate first weekly plan + posts after registration."""
    try:
        from content_planner import generate_weekly_plan
        from services.user_scheduler import activate_user_schedule

        plan = await generate_weekly_plan(niche, user_id)
        await activate_user_schedule(user_id)

        await bot.send_message(
            user_id,
            f"✅ Апталық жоспар дайын! {len(plan)} тақырып жасалды.\n"
            f"Посттар генерацияланады, жақында аласың...",
        )

        # Generate posts for all plan items
        from post_generator import generate_post_and_save
        from image_generator import generate_image

        for item in plan:
            try:
                post_data = await generate_post_and_save(item, user_id)
                await bot.send_message(user_id, "⏳ Пост жасалды, сурет генерацияланады...")
                await generate_image(post_data["image_prompt"], post_data["id"])
                # Send for moderation
                from handlers.moderation import send_post_preview_to_user
                await send_post_preview_to_user(bot, post_data["id"], user_id)
            except Exception as e:
                logger.error("Bootstrap post error user_id=%d: %s", user_id, e)

    except Exception as e:
        logger.error("Bootstrap error user_id=%d: %s", user_id, e)
        await bot.send_message(user_id, f"❌ Контент жасауда қате: {e}")
