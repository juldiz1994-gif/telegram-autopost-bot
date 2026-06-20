import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, PhotoSize

from config import config
from database import db

logger = logging.getLogger(__name__)
payments_router = Router()


class PaymentState(StatesGroup):
    waiting_for_check = State()


@payments_router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Тіркелмегенсің. /start жаз.")
        return
    if user["status"] == "active":
        await message.answer(
            f"✅ Жазылымың белсенді: {user['subscription_ends_at'].strftime('%d.%m.%Y')}-ге дейін."
        )
        return
    await state.set_state(PaymentState.waiting_for_check)
    await message.answer(
        f"💳 <b>Жазылым төлемі</b>\n\n"
        f"Сома: <b>1990 тг/ай</b>\n"
        f"📱 Kaspi: <code>{config.KASPI_PHONE}</code>\n"
        f"👤 Аты: <b>{config.KASPI_NAME}</b>\n\n"
        f"Аударғаннан кейін осы чатқа <b>чек суретін жібер</b> — "
        f"30 минут ішінде растаймыз.",
        parse_mode="HTML",
    )


@payments_router.message(PaymentState.waiting_for_check, F.photo)
async def handle_check_photo(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user:
        return

    if user["status"] == "active":
        await message.answer("✅ Жазылымың белсенді, төлем қажет емес.")
        return

    # Take highest resolution photo
    photo: PhotoSize = message.photo[-1]
    file_id = photo.file_id

    payment_id = await db.save_payment(user_id=user["id"], check_file_id=file_id)

    await message.answer(
        "✅ Чек алынды! Жақын арада растаймыз (30 мин ішінде)."
    )

    # Notify admin
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Растау", callback_data=f"pay_confirm:{payment_id}"
        ),
        InlineKeyboardButton(
            text="❌ Қабылдамау", callback_data=f"pay_reject:{payment_id}"
        ),
    ]])

    name = user.get("full_name") or user.get("username") or str(user["id"])
    username_str = f"@{user['username']}" if user.get("username") else "username жоқ"

    await message.bot.send_photo(
        chat_id=config.TELEGRAM_ADMIN_ID,
        photo=file_id,
        caption=(
            f"💳 <b>Жаңа төлем!</b>\n\n"
            f"👤 {name} ({username_str})\n"
            f"📋 Ниша: {user['niche']}\n"
            f"📅 Тіркелген: {user['created_at'].strftime('%d.%m.%Y')}\n"
            f"🆔 Төлем ID: {payment_id}"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    logger.info("Payment id=%d from user_id=%d sent to admin", payment_id, user["id"])
