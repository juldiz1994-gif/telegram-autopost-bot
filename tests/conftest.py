"""
Set required environment variables before any test imports database/config.
This allows unit tests to run without a real .env file or Railway secrets.
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "-1001234567890")
os.environ.setdefault("TELEGRAM_ADMIN_ID", "999999")
os.environ.setdefault("CONTENT_NICHE", "test-niche")
os.environ.setdefault("KASPI_PHONE", "+77001234567")
