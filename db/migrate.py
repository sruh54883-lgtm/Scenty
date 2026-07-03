"""Применяет schema.sql через asyncpg + обновляет пароль admin через passlib."""
import asyncio
import os
from pathlib import Path


async def migrate():
    import asyncpg
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL not set — skipping migration")
        return
    sql = (Path(__file__).parent / "schema.sql").read_text()
    ssl = "require" if "sslmode=require" in url else None
    conn = await asyncpg.connect(url, timeout=15, ssl=ssl)
    try:
        await conn.execute(sql)
        # Разрешаем agent_id = NULL для ручных транзакций от администратора
        await conn.execute(
            "ALTER TABLE transactions ALTER COLUMN agent_id DROP NOT NULL"
        )
        # Прогрессивный кешбэк: колонка для хранения применённого процента
        await conn.execute(
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cashback_percent SMALLINT NOT NULL DEFAULT 10"
        )
        # Уникальность номера телефона клиента
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL"
        )
        # Связываем старые записи cashback_spends с gift_requests:
        # совпадение по user_id + сумме подарка + минимальной разнице времени (одна транзакция)
        await conn.execute(
            """
            UPDATE cashback_spends cs
            SET gift_request_id = (
                SELECT gr.id
                FROM gift_requests gr
                JOIN gifts g ON g.id = gr.gift_id
                WHERE gr.user_id = cs.user_id
                  AND g.price_cashback = cs.amount
                ORDER BY ABS(EXTRACT(EPOCH FROM (gr.created_at - cs.created_at)))
                LIMIT 1
            )
            WHERE cs.gift_request_id IS NULL
              AND EXISTS (
                SELECT 1 FROM gift_requests gr
                JOIN gifts g ON g.id = gr.gift_id
                WHERE gr.user_id = cs.user_id
                  AND g.price_cashback = cs.amount
              )
            """
        )
        # Удаляем расходы для уже отклонённых заявок — они не должны считаться в "Потрачено"
        deleted = await conn.execute(
            """
            DELETE FROM cashback_spends cs
            WHERE cs.gift_request_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM gift_requests gr
                WHERE gr.id = cs.gift_request_id
                  AND gr.status = 'rejected'
              )
            """
        )
        print(f"cashback_spends cleanup OK: {deleted}")
        print("DB schema applied OK")

        # Сбрасываем пароль admin через passlib (bcrypt $2b$), не pgcrypto
        try:
            sys_path = str(Path(__file__).parent.parent / "api")
            import sys
            if sys_path not in sys.path:
                sys.path.insert(0, sys_path)
            from auth import hash_password
            admin_pass = os.environ.get("ADMIN_SECRET") or os.environ.get("ADMIN_PASSWORD", "")
            if not admin_pass:
                raise RuntimeError("ADMIN_SECRET env var is required for admin setup")
            await conn.execute(
                """
                INSERT INTO admin_users (username, password_hash, is_superadmin)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (username) DO UPDATE
                  SET password_hash = EXCLUDED.password_hash, is_superadmin = TRUE
                """,
                "admin",
                hash_password(admin_pass),
            )
            deleted = await conn.execute("DELETE FROM admin_users WHERE username != 'admin'")
            print("Admin password reset OK")
            print(f"Old admin accounts removed: {deleted}")

            shox_pass = os.environ.get("SHOX_PASS", "Shox2026!")
            await conn.execute(
                "UPDATE agents SET password_hash=$1, is_active=TRUE WHERE username='shox'",
                hash_password(shox_pass),
            )
            print("Agent shox password reset OK")
        except Exception as e:
            print(f"Admin setup warning: {e}")

        # Добавляем S-100 если ещё нет
        try:
            await conn.execute(
                """INSERT INTO diffusers
                  (name_ru, name_uz, description_ru, description_uz,
                   type, tag_ru, tag_uz, image_url, sort_order)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (name_ru) DO NOTHING""",
                "S-100", "S-100",
                ("✅ Эффективное ароматизирование до 100 м²\n"
                 "✅ 400 мл аромакапсула — спокойствие и комфорт на недели\n"
                 "✅ Работает от электричества — надёжность без перебоев\n"
                 "✅ Управление через мобильное приложение — всё в ваших руках"),
                ("✅ 100 m² gacha samarali iforlantirish\n"
                 "✅ 400 ml aroma kapsula — haftalab tinchlik va huzur\n"
                 "✅ Elektrda ishlaydi — uzluksiz barqarorlik\n"
                 "✅ Mobil ilova orqali boshqaruv — barchasi qo'lingizda"),
                "device", "До 100 кв.м", "100 m² gacha", "", 20,
            )
            print("S-100 diffuser seeded OK")
        except Exception as e:
            print(f"S-100 seed note: {e}")

    except Exception as e:
        print(f"Migration note: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
