"""Конфигурация приложения из переменных окружения."""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name, "")
    if not val:
        raise RuntimeError(f"Required env var {name!r} is not set")
    return val


class Settings:
    BOT_TOKEN: str = _require("BOT_TOKEN")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost:5432/scenti")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "http://localhost:8080")
    ADMIN_SECRET: str = _require("ADMIN_SECRET")
    JWT_SECRET: str = _require("JWT_SECRET")

    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24  # сокращено с 7 дней до 24 часов

    CASHBACK_PERCENT: int = 10


settings = Settings()
