#!/bin/bash
set -e

# Абсолютный путь к корню проекта
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "=== Scenti startup: ROOT=$ROOT ==="

# Применяем схему БД через Python (psql может не быть)
echo ">>> Applying DB schema..."
timeout 8 python3 "$ROOT/db/migrate.py" || echo "Migration warning (ignored)"

# Запускаем бота в фоне
echo ">>> Starting Telegram bot..."
cd "$ROOT/bot" && python main.py &
BOT_PID=$!
cd "$ROOT"

# Запускаем FastAPI (основной процесс)
echo ">>> Starting FastAPI on port ${PORT:-8000}..."
cd "$ROOT/api" && exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
