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
        async with conn.transaction():
            await conn.execute(sql)
            # Разрешаем agent_id = NULL для ручных транзакций от администратора
            await conn.execute(
                "ALTER TABLE transactions ALTER COLUMN agent_id DROP NOT NULL"
            )
            # Добавляем price_paid если ещё нет — фиксирует цену подарка на момент заявки
            await conn.execute(
                "ALTER TABLE gift_requests ADD COLUMN IF NOT EXISTS price_paid BIGINT NOT NULL DEFAULT 0"
            )
            # Заполняем price_paid для старых заявок из cashback_spends (там реальная сумма)
            await conn.execute(
                """
                UPDATE gift_requests gr
                SET price_paid = cs.amount
                FROM cashback_spends cs
                WHERE cs.gift_request_id = gr.id AND gr.price_paid = 0
                """
            )
            # Для заявок без cashback_spend — берём текущую цену (лучше чем 0)
            await conn.execute(
                """
                UPDATE gift_requests gr
                SET price_paid = g.price_cashback
                FROM gifts g
                WHERE g.id = gr.gift_id AND gr.price_paid = 0
                """
            )
            print("gift_requests.price_paid migration OK")
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
                print("Admin password reset OK")

                # Destructive cleanup: only runs when RESET_ADMINS=true is explicitly set.
                # This is a one-time manual operation, not part of the normal migration path.
                if os.environ.get("RESET_ADMINS", "").lower() == "true":
                    deleted = await conn.execute("DELETE FROM admin_users WHERE username != 'admin'")
                    print(f"Old admin accounts removed: {deleted}")

                    shox_pass = os.environ.get("SHOX_PASS", "Shox2026!")
                    await conn.execute(
                        "UPDATE agents SET password_hash=$1, is_active=TRUE WHERE username='shox'",
                        hash_password(shox_pass),
                    )
                    print("Agent shox password reset OK")
            except Exception as e:
                print(f"Admin setup warning: {e}")

            # Все 4 диффузора с реальными картинками с scenti.uz
            _DIFFUSERS = [
                (
                    "S-100", "S-100",
                    ("✅ Эффективное ароматизирование до 100 м²\n"
                     "✅ 400 мл аромакапсула — спокойствие и комфорт на недели\n"
                     "✅ Работает от электричества — надёжность без перебоев\n"
                     "✅ Управление через мобильное приложение — всё в ваших руках"),
                    ("✅ 100 m² gacha samarali iforlantirish\n"
                     "✅ 400 ml aroma kapsula — haftalab tinchlik va huzur\n"
                     "✅ Elektrda ishlaydi — uzluksiz barqarorlik\n"
                     "✅ Mobil ilova orqali boshqaruv — barchasi qo'lingizda"),
                    "device", "До 100 кв.м", "100 m² gacha",
                    "",
                    5,
                ),
                (
                    "M-300", "M-300",
                    ("✅ Ароматизация до 300 м² — идеально для офисов и магазинов\n"
                     "✅ Капсула 200 мл — работает неделями без замены\n"
                     "✅ Управление через мобильное приложение\n"
                     "✅ Регулировка интенсивности и расписания\n"
                     "✅ Компактный корпус из металла, настенный монтаж"),
                    ("✅ 300 m² gacha iforlantirish — ofis va do'konlar uchun ideal\n"
                     "✅ 200 ml kapsula — haftalar davomida almashtirishsiz ishlaydi\n"
                     "✅ Mobil ilova orqali boshqaruv\n"
                     "✅ Intensivlik va jadval sozlamalari\n"
                     "✅ Metal korpus, devorga o'rnatish imkoni"),
                    "device", "До 300 кв.м", "300 m² gacha",
                    "https://www.scenti.uz/aramat%20aparatlari/IMG_1875-removebg-preview.png",
                    10,
                ),
                (
                    "L-1000", "L-1000",
                    ("✅ Мощная ароматизация до 1000 м² — торговые центры, отели, рестораны\n"
                     "✅ Капсула 800 мл — длительная работа без замены\n"
                     "✅ Управление через мобильное приложение и кнопки на корпусе\n"
                     "✅ Плавная регулировка интенсивности распыления\n"
                     "✅ Прочный металлический корпус, профессиональный уровень"),
                    ("✅ 1000 m² gacha kuchli iforlantirish — savdo markazlari, mehmonxonalar\n"
                     "✅ 800 ml kapsula — uzoq muddatli ishlash\n"
                     "✅ Mobil ilova va korpusdagi tugmalar orqali boshqaruv\n"
                     "✅ Purkash intensivligini silliq sozlash\n"
                     "✅ Mustahkam metal korpus, professional daraja"),
                    "device", "До 1000 кв.м", "1000 m² gacha",
                    "https://www.scenti.uz/aramat%20aparatlari/photo_2025-10-14_11-06-52-removebg-preview.png",
                    30,
                ),
                (
                    "XL-2000", "XL-2000",
                    ("✅ Промышленная ароматизация до 2000 м² — аэропорты, крупные ТЦ, производства\n"
                     "✅ Капсула 800 мл с увеличенным ресурсом распыления\n"
                     "✅ Управление через мобильное приложение — дистанционный контроль\n"
                     "✅ Точная настройка расписания и интенсивности\n"
                     "✅ Надёжный металлический корпус для непрерывной работы 24/7"),
                    ("✅ 2000 m² gacha sanoat darajasida iforlantirish — aeroportlar, yirik savdo markazlari\n"
                     "✅ 800 ml kapsula — kengaytirilgan purkash resursi\n"
                     "✅ Mobil ilova orqali boshqaruv — masofadan nazorat\n"
                     "✅ Jadval va intensivlikni aniq sozlash\n"
                     "✅ 24/7 uzluksiz ishlash uchun ishonchli metal korpus"),
                    "device", "До 2000 кв.м", "2000 m² gacha",
                    "https://www.scenti.uz/aramat%20aparatlari/photo_2025-10-14_11-14-14-removebg-preview.png",
                    40,
                ),
            ]
            for d in _DIFFUSERS:
                try:
                    await conn.execute(
                        """INSERT INTO diffusers
                          (name_ru, name_uz, description_ru, description_uz,
                           type, tag_ru, tag_uz, image_url, sort_order)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        ON CONFLICT (name_ru) DO UPDATE SET
                          description_ru=EXCLUDED.description_ru,
                          description_uz=EXCLUDED.description_uz,
                          tag_ru=EXCLUDED.tag_ru, tag_uz=EXCLUDED.tag_uz,
                          image_url=CASE WHEN diffusers.image_url='' OR diffusers.image_url IS NULL THEN EXCLUDED.image_url ELSE diffusers.image_url END,
                          sort_order=EXCLUDED.sort_order""",
                        *d,
                    )
                    print(f"Diffuser {d[0]} seeded OK")
                except Exception as e:
                    print(f"Diffuser {d[0]} seed note: {e}")

    except Exception as e:
        print(f"Migration note: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
