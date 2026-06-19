import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_create_user_inserts_row():
    """create_user executes INSERT with correct params."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    await db.create_user(
        user_id=123456,
        username="testuser",
        full_name="Test User",
        niche="Психология",
        channel_id=-1001234567890,
        channel_title="Psych Blog",
        post_frequency=2,
        publish_times="10:00,18:00",
    )
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0]
    assert "INSERT INTO users" in call_args[0]
    assert 123456 in call_args


@pytest.mark.asyncio
async def test_get_user_returns_record():
    """get_user fetches row by Telegram ID."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": 123456, "niche": "Психология", "status": "trial"}
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    result = await db.get_user(123456)
    assert result["niche"] == "Психология"
    mock_conn.fetchrow.assert_called_once_with("SELECT * FROM users WHERE id = $1", 123456)


@pytest.mark.asyncio
async def test_save_payment_returns_id():
    """save_payment inserts row and returns generated id."""
    from database import Database
    db = Database()
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = 42
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    db._pool = mock_pool

    payment_id = await db.save_payment(user_id=123456, check_file_id="AgACAgI...")
    assert payment_id == 42
    mock_conn.fetchval.assert_called_once()
