import os
import logging
from datetime import time
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    def __init__(self) -> None:
        self.GEMINI_API_KEY: str = self._require("GEMINI_API_KEY")
        self.GEMINI_TEXT_MODEL: str = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
        self.GEMINI_IMAGE_MODEL: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-preview-05-20")
        self.DATABASE_URL: str = self._require("DATABASE_URL")
        self.TELEGRAM_BOT_TOKEN: str = self._require("TELEGRAM_BOT_TOKEN")
        self.TELEGRAM_CHANNEL_ID: str = self._require("TELEGRAM_CHANNEL_ID")
        self.TELEGRAM_ADMIN_ID: int = int(self._require("TELEGRAM_ADMIN_ID"))
        self.CONTENT_NICHE: str = self._require("CONTENT_NICHE")
        self.PUBLISH_TIMES: list[time] = self._parse_times(
            os.getenv("PUBLISH_TIMES", "10:00,18:00")
        )
        self.TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Almaty")
        self.IMAGE_SIZE: str = "1024x1024"

    def _require(self, name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ValueError(
                f"Міндетті '{name}' айнымалысы орнатылмаған. "
                f".env файлын немесе Railway Variables тексеріңіз."
            )
        return value

    def _parse_times(self, times_str: str) -> list[time]:
        result = []
        for t in times_str.split(","):
            t = t.strip()
            hour, minute = t.split(":")
            result.append(time(int(hour), int(minute)))
        return result


config = Config()
