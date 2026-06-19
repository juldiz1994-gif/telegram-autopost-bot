from scheduler import get_scheduler
from database import db


async def activate_user_schedule(user_id: int) -> None:
    """Called when user registers or payment confirmed."""
    user = await db.get_user(user_id)
    if user:
        get_scheduler().add_user_jobs(dict(user))


async def deactivate_user_schedule(user_id: int) -> None:
    """Called when user expires or is blocked."""
    get_scheduler().remove_user_jobs(user_id)
