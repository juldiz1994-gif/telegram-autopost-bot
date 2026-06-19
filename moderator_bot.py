from aiogram import Dispatcher

from handlers.admin import AdminOnlyMiddleware, admin_router
from handlers.moderation import moderation_router
from handlers.onboarding import onboarding_router
from handlers.payments import payments_router


def setup_dispatcher(dp: Dispatcher) -> None:
    # Admin router: protected by AdminOnlyMiddleware
    admin_router.message.middleware(AdminOnlyMiddleware())
    admin_router.callback_query.middleware(AdminOnlyMiddleware())

    # Order matters: onboarding first (handles /start and FSM),
    # then payments (photo handler), then moderation (callbacks),
    # then admin (protected commands)
    dp.include_router(onboarding_router)
    dp.include_router(payments_router)
    dp.include_router(moderation_router)
    dp.include_router(admin_router)
