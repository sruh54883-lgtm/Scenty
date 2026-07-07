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
        # Дата последней регистрации — для сброса кешбэк-тира при перерегистрации
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS cashback_reset_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        )
        # Выбор аппарата (диффузора) при регистрации
        await conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS diffuser_id INT REFERENCES diffusers(id)"
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
        # Постоянный Telegram file_id для документа политики конфиденциальности
        await conn.execute(
            "ALTER TABLE privacy_policy ADD COLUMN IF NOT EXISTS tg_file_id VARCHAR(200) NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "ALTER TABLE privacy_policy ADD COLUMN IF NOT EXISTS file_url_uz VARCHAR(500) NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "ALTER TABLE privacy_policy ADD COLUMN IF NOT EXISTS tg_file_id_uz VARCHAR(200) NOT NULL DEFAULT ''"
        )
        # Установить файлы политики по умолчанию если ещё не заданы
        await conn.execute("""
            UPDATE privacy_policy
            SET file_url = '/admin/policy_ru.docx',
                file_url_uz = '/admin/policy_uz.docx'
            WHERE (file_url IS NULL OR file_url = '')
               OR (file_url_uz IS NULL OR file_url_uz = '')
        """)
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

        except Exception as e:
            print(f"Admin setup warning: {e}")

        # Диффузоры (аппараты)
        _devices = [
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
                "До 100 кв.м", "100 m² gacha", 10,
            ),
            (
                "M-300", "M-300",
                ("✅ Эффективное ароматизирование до 200 м²\n"
                 "✅ 200 мл аромакапсула — длительная работа без замены\n"
                 "✅ Корпус из металла — надёжность и премиальный вид\n"
                 "✅ Управление через мобильное приложение и кнопки\n"
                 "✅ Регулировка интенсивности и времени работы"),
                ("✅ 200 m² gacha samarali iforlantirish\n"
                 "✅ 200 ml aroma kapsula — uzoq muddatli uzluksiz ish\n"
                 "✅ Metall korpus — ishonchlilik va premium ko'rinish\n"
                 "✅ Mobil ilova va tugmalar orqali boshqaruv\n"
                 "✅ Intensivlik va ish vaqtini sozlash imkoniyati"),
                "До 200 кв.м", "200 m² gacha", 20,
            ),
            (
                "L-1000", "L-1000",
                ("✅ Мощное ароматизирование до 1000 м²\n"
                 "✅ 800 мл аромакапсула — максимальная автономность\n"
                 "✅ Корпус из металла — профессиональный стандарт\n"
                 "✅ Управление через мобильное приложение и кнопки\n"
                 "✅ Гибкая настройка интенсивности и времени работы"),
                ("✅ 1000 m² gacha kuchli iforlantirish\n"
                 "✅ 800 ml aroma kapsula — maksimal avtonomlik\n"
                 "✅ Metall korpus — professional standart\n"
                 "✅ Mobil ilova va tugmalar orqali boshqaruv\n"
                 "✅ Intensivlik va ish vaqtini moslashuvchan sozlash"),
                "До 1000 кв.м", "1000 m² gacha", 30,
            ),
            (
                "XL-2000", "XL-2000",
                ("✅ Профессиональное ароматизирование до 2000 м²\n"
                 "✅ 800 мл аромакапсула — для крупных объектов\n"
                 "✅ Корпус из металла — высочайшая надёжность\n"
                 "✅ Управление через мобильное приложение и кнопки\n"
                 "✅ Программируемое расписание и настройка интенсивности"),
                ("✅ 2000 m² gacha professional iforlantirish\n"
                 "✅ 800 ml aroma kapsula — yirik ob'ektlar uchun\n"
                 "✅ Metall korpus — eng yuqori ishonchlilik\n"
                 "✅ Mobil ilova va tugmalar orqali boshqaruv\n"
                 "✅ Dasturlanadigan jadval va intensivlikni sozlash"),
                "До 2000 кв.м", "2000 m² gacha", 40,
            ),
        ]
        for d in _devices:
            try:
                await conn.execute(
                    """INSERT INTO diffusers
                      (name_ru, name_uz, description_ru, description_uz,
                       type, tag_ru, tag_uz, image_url, sort_order)
                    VALUES ($1,$2,$3,$4,'device',$5,$6,'',$7)
                    ON CONFLICT (name_ru) DO NOTHING""",
                    d[0], d[1], d[2], d[3], d[4], d[5], d[6],
                )
                print(f"Diffuser seeded: {d[0]}")
            except Exception as e:
                print(f"Diffuser seed note ({d[0]}): {e}")

        # Ароматы
        _aroma_desc_ru = (
            "Натуральный аромат премиум-класса для диффузоров Scenti. "
            "Создаёт неповторимую атмосферу свежести и уюта в вашем помещении. "
            "Совместим со всеми моделями аппаратов Scenti."
        )
        _aroma_desc_uz = (
            "Scenti diffuzorlari uchun premium sifatli tabiiy xushbo'y. "
            "Xonangizda yangilik va qulaylik atmosferasini yaratadi. "
            "Barcha Scenti apparat modellari bilan mos keladi."
        )
        _aromas = [
            ("Inbir", "Inbir"),
            ("Hilton", "Hilton"),
            ("Atlantic", "Atlantic"),
            ("Dark Tea", "Dark Tea"),
            ("Harmony", "Harmony"),
            ("Monaco", "Monaco"),
            ("Burberry", "Burberry"),
            ("Gulong", "Gulong"),
            ("California", "California"),
            ("Rosso", "Rosso"),
            ("Premier", "Premier"),
            ("My Path", "My Path"),
            ("Adress", "Adress"),
            ("Unique 02", "Unique 02"),
            ("White Tea", "White Tea"),
            ("Armani", "Armani"),
            ("Bengal", "Bengal"),
            ("Miss Dior", "Miss Dior"),
            ("Velvet", "Velvet"),
            ("Azure", "Azure"),
            ("Bubble Gum", "Bubble Gum"),
            ("Pumpkin Pie", "Pumpkin Pie"),
            ("Resin", "Resin"),
            ("Currant", "Currant"),
            ("Unique 04", "Unique 04"),
            ("Master", "Master"),
            ("Cappuccino", "Cappuccino"),
            ("Green Bamboo", "Green Bamboo"),
            ("Ritz", "Ritz"),
            ("Cookies", "Cookies"),
            ("Desert", "Desert"),
            ("Soft Tule", "Soft Tule"),
            ("Crystal", "Crystal"),
            ("Embers", "Embers"),
            ("Passion", "Passion"),
            ("Diamond", "Diamond"),
            ("Caramel", "Caramel"),
            ("Marshmallow", "Marshmallow"),
            ("Shadow", "Shadow"),
            ("Candy", "Candy"),
        ]
        for i, (name_ru, name_uz) in enumerate(_aromas):
            try:
                await conn.execute(
                    """INSERT INTO diffusers
                      (name_ru, name_uz, description_ru, description_uz,
                       type, tag_ru, tag_uz, image_url, sort_order)
                    VALUES ($1,$2,$3,$4,'aroma','Аромат','Xushbo''y','',$5)
                    ON CONFLICT (name_ru) DO NOTHING""",
                    name_ru, name_uz, _aroma_desc_ru, _aroma_desc_uz, 100 + i,
                )
                print(f"Aroma seeded: {name_ru}")
            except Exception as e:
                print(f"Aroma seed note ({name_ru}): {e}")

        # Дефолтные настройки контактов
        _default_settings = [
            ("contact_phone", "+998773831111"),
            ("contact_phone_display", "+998 77 383 11 11"),
            ("contact_tg", "Scentioffice1"),
            ("notifications_chat_id", ""),
            ("youtube_url", ""),
            ("instagram_url", ""),
        ]
        for key, value in _default_settings:
            try:
                await conn.execute(
                    "INSERT INTO app_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO NOTHING",
                    key, value,
                )
            except Exception as e:
                print(f"Settings seed note ({key}): {e}")
        print("Default settings seeded OK")

    except Exception as e:
        print(f"Migration note: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
