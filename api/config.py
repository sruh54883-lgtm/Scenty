"""Конфигурация приложения из переменных окружения."""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost:5432/scenti")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "http://localhost:8080")
    ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", "changeme")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "scenti_jwt_secret_2026")

    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24 * 7  # 7 дней

    CASHBACK_PERCENT: int = 10  # процент кешбэка


settings = Settings()
