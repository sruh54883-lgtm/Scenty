#!/bin/bash
set -e

echo "=== Scenti startup ==="

# Применяем схему БД (игнорируем ошибки если таблицы уже есть)
echo ">>> Applying DB schema..."
psql "$DATABASE_URL" -f db/schema.sql 2>/dev/null || true

# Запускаем бота в фоне
echo ">>> Starting Telegram bot..."
cd bot && python main.py &
BOT_PID=$!
cd ..

# Запускаем FastAPI (основной процесс — Railway следит за ним)
echo ">>> Starting FastAPI on port $PORT..."
cd api && exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
