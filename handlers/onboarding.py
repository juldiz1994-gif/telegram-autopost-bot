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
    waiting_cta_type = State()
    waiting_cta_text = State()


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 рет", callback_data="freq:1"),
        InlineKeyboardButton(text="2 рет", callback_data="freq:2"),
    ]])


def _cta_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Курсыма шақыру", callback_data="cta_type:course")],
        [InlineKeyboardButton(text="📢 Каналыма шақыру", callback_data="cta_type:channel")],
        [InlineKeyboardButton(text="🚫 Керек емес", callback_data="cta_type:none")],
    ])


def _confirm_channel_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Иә, осы", callback_data=f"chan_confirm:{channel_id}"),
        InlineKeyboardButton(text="❌ Жоқ, басқасы", callback_data="chan_retry"),
    ]])


@onboarding_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if user:
        status = user["status"]
        if status == "trial" and user.get("trial_ends_at"):
            status_line = f"⏳ Сынақ мерзімі: {user['trial_ends_at'].strftime('%d.%m.%Y')}-ге дейін"
        elif status == "active" and user.get("subscription_ends_at"):
            status_line = f"✅ Белсенді: {user['subscription_ends_at'].strftime('%d.%m.%Y')}-ге дейін"
        elif status == "expired":
            status_line = "❌ Мерзімі өткен. Жалғастыру үшін чек жібер."
        elif status == "blocked":
            status_line = "⛔ Бұғатталған. Қолдауға хабарлас."
        else:
            status_line = f"📌 Статус: {status}"
        await message.answer(
            f"👋 Қайта келдің!\n\n"
            f"📋 Ниша: {user['niche']}\n"
            f"{status_line}\n\n"
            f"📬 /queue — посттар кезегі\n"
            f"📊 /my_stats — статистика",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💬 Техподдержка", url="https://www.instagram.com/ai_aisha_kz?igsh=NHV4ZW85cGxtNHJr"),
            ]]),
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💬 Техподдержка", url="https://www.instagram.com/ai_aisha_kz?igsh=NHV4ZW85cGxtNHJr"),
        ]]),
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
    publish_times = "10:00,18:00" if freq == 2 else "10:00"
    await state.update_data(freq=freq, publish_times=publish_times)
    await state.set_state(OnboardingState.waiting_cta_type)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "📌 <b>Пост соңына не қосамыз?</b>\n\n"
        "Оқырмандарды ненге шақырайық?",
        parse_mode="HTML",
        reply_markup=_cta_type_keyboard(),
    )


@onboarding_router.callback_query(
    OnboardingState.waiting_cta_type,
    F.data.startswith("cta_type:")
)
async def cb_cta_type(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    cta_type = callback.data.split(":")[1]
    await callback.message.edit_reply_markup(reply_markup=None)

    if cta_type == "none":
        await state.update_data(cta="")
        await _finish_registration(callback, state)
    elif cta_type == "channel":
        await state.update_data(cta="📢 Осы каналды достарыңмен бөліс — бірге өсеміз!")
        await _finish_registration(callback, state)
    elif cta_type == "course":
        await state.set_state(OnboardingState.waiting_cta_text)
        await callback.message.answer(
            "📚 Курсыңыз туралы қысқаша жазыңыз.\n\n"
            "Мысалы: «Менің 30 күндік стресстен арылу курсым бар, "
            "нәтижені кепілдік беремін. Жазылу үшін @username-ге хабарлас»",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⏭ Бас тарту", callback_data="cta_skip"),
            ]]),
        )


@onboarding_router.callback_query(
    OnboardingState.waiting_cta_text,
    F.data == "cta_skip"
)
async def cb_cta_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    niche = data.get("niche", "")
    cta = f"📚 Менің {niche} бойынша курсым бар — саған шақырамын!"
    await state.update_data(cta=cta)
    await _finish_registration(callback, state)


@onboarding_router.message(OnboardingState.waiting_cta_text)
async def process_cta_text(message: Message, state: FSMContext) -> None:
    cta = (message.text or "").strip()
    await state.update_data(cta=cta)
    await _finish_registration(message, state)


async def _finish_registration(event, state: FSMContext) -> None:
    data = await state.get_data()
    freq = data["freq"]
    publish_times = data["publish_times"]
    cta = data.get("cta", "")

    if hasattr(event, "from_user"):
        user = event.from_user
        answer = event.answer if hasattr(event, "answer") else event.message.answer
        bot = event.bot if hasattr(event, "bot") else event.message.bot
    else:
        user = event.from_user
        answer = event.message.answer
        bot = event.message.bot

    await db.create_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        niche=data["niche"],
        channel_id=data["channel_id"],
        channel_title=data["channel_title"],
        post_frequency=freq,
        publish_times=publish_times,
        cta=cta,
    )
    await state.clear()

    from datetime import datetime, timedelta
    trial_end = datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)

    await answer(
        f"🎉 <b>Тіркеу аяқталды!</b>\n\n"
        f"📋 Ниша: {data['niche']}\n"
        f"📢 Канал: {data['channel_title']}\n"
        f"📅 Постар: күніне {freq} рет ({publish_times})\n"
        f"⏳ Тегін мерзім: {trial_end.strftime('%d.%m.%Y')}-ге дейін\n\n"
        f"⏳ Контент-жоспар жасалуда...",
        parse_mode="HTML",
    )

    asyncio.create_task(_bootstrap_user(bot, user.id, data["niche"]))


async def _generate_and_moderate(bot: Bot, post_data: dict, user_id: int) -> None:
    try:
        from image_generator import generate_image
        await generate_image(post_data["image_prompt"], post_data["id"])
        await db.update_post_status(post_data["id"], "approved")
    except Exception as e:
        logger.error("Generate+approve error user_id=%d post_id=%d: %s", user_id, post_data["id"], e)


async def _bootstrap_user(bot: Bot, user_id: int, niche: str) -> None:
    """Generate first weekly plan + posts after registration."""
    try:
        from content_planner import generate_weekly_plan
        from post_generator import generate_post_and_save
        from services.user_scheduler import activate_user_schedule

        plan = await generate_weekly_plan(niche, user_id)
        await activate_user_schedule(user_id)

        await bot.send_message(
            user_id,
            f"✅ Апталық жоспар дайын! {len(plan)} тақырып жасалды.\n"
            f"⏳ Посттар мен суреттер жасалуда, кесте бойынша каналда жарияланады...",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📬 Посттарды көру", callback_data="show_queue"),
            ]]),
        )

        for item in plan:
            try:
                post_data = await generate_post_and_save(item, user_id)
                asyncio.create_task(_generate_and_moderate(bot, post_data, user_id))
                await asyncio.sleep(1)
            except Exception as e:
                logger.error("Bootstrap post error user_id=%d: %s", user_id, e)

    except Exception as e:
        logger.error("Bootstrap error user_id=%d: %s", user_id, e)
        await bot.send_message(user_id, f"❌ Контент жасауда қате: {e}")


@onboarding_router.callback_query(F.data == "show_queue")
async def cb_show_queue(callback: CallbackQuery) -> None:
    from database import db
    from handlers.moderation import _queue_keyboard
    user_id = callback.from_user.id
    approved = await db.get_posts_by_status_for_user(user_id, "approved")
    pending = await db.get_posts_by_status_for_user(user_id, "pending_review")
    all_posts = list(pending) + list(approved)
    if not all_posts:
        await callback.message.answer("📬 Кезек бос.")
        await callback.answer()
        return
    lines = ["📬 <b>Посттар кезегі</b> — оқу үшін басыңыз:\n"]
    if pending or approved:
        lines.append(f"⏳ Қарауда: {len(pending)}    ✅ Бекітілген: {len(approved)}")
    await callback.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=_queue_keyboard(all_posts))
    await callback.answer()
