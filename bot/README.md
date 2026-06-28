# Scenti — Telegram Bot

Бот программы лояльности Scenti на **aiogram 3**. Работает с той же
PostgreSQL-БД, что и FastAPI (`db/schema.sql`).

## Запуск

```bash
cd bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py
```

`.env` читается из **корня проекта** (`../.env`). Нужные ключи — см. `.env.example`.

## Что реализовано

- **Регистрация (FSM)**: политика → имя → бизнес → телефон → регион → район.
- `/start` определяет состояние пользователя по `users.telegram_id`:
  не найден → регистрация; найден без согласия/района → продолжение; готов → меню.
- **Главное меню** с WebApp-кнопкой «🌿 Открыть Scenti» (`WEBAPP_URL`).
  WebApp-кнопка показывается только при HTTPS; на `http://localhost` отдаётся
  ссылка текстом (Telegram запрещает web_app по http).
- Команды `/help`, `/cancel`.
- **Фоллбэк БД**: если asyncpg не подключился, бот стартует и отвечает
  «Сервис временно недоступен».

## Уведомления (для FastAPI)

`bot/notifications.py` — вызываются из API после подтверждения транзакций:

```python
from bot import notifications
await notifications.notify_user_cashback_credited(tg_id, amount, balance)
await notifications.notify_user_gift_status(tg_id, "Диффузор Aroma", "approved")
await notifications.notify_admin_new_transaction(admin_tg_id, "Ali", 50000)
await notifications.broadcast_message("Текст рассылки", [111, 222, 333])
await notifications.close_bot()   # на shutdown FastAPI
```

`broadcast_message` соблюдает ~20 сообщений/сек и возвращает
`{'sent': N, 'failed': M}`.

## Структура

```
bot/
├── main.py              запуск, polling
├── config.py            настройки из ../.env
├── database.py          asyncpg pool + доменные запросы (+ фоллбэк)
├── states.py            FSM состояния регистрации
├── notifications.py     исходящие уведомления и рассылка
├── keyboards/
│   ├── reply.py         запрос контакта, главное меню (WebApp)
│   └── inline.py        политика, регионы, районы (по 3 в ряд)
└── handlers/
    ├── start.py         /start + FSM регистрации
    ├── menu.py          главное меню + баланс
    └── common.py        /help, /cancel
```

## Безопасность

- Все секреты — в `.env` (в коде не хардкодятся).
- Все SQL — параметризованные (`$1, $2, ...`), без конкатенации;
  выбор колонки локали — из белого списка.
- Валидация ввода: длина имени/бизнеса, проверка собственного контакта,
  безопасный парсинг `callback_data`.
- Ошибки логируются, пользователю — нейтральное сообщение (без stack trace).
```
