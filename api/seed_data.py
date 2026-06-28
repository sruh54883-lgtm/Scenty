"""
Наполнение БД тестовыми данными.
Запуск: python seed_data.py
Использует DATABASE_URL из .env.
"""
import asyncio
import json

import asyncpg

from auth import hash_password
from config import settings


async def main():
    conn = await asyncpg.connect(settings.DATABASE_URL)
    print("Подключено к БД:", settings.DATABASE_URL)

    # ---------- Подарки ----------
    gifts = [
        {
            "name_ru": "Набор аромасвечей",
            "name_uz": "Aromali shamlar to'plami",
            "description_ru": "Подарочный набор из 3 ароматических свечей ручной работы.",
            "description_uz": "Qo'lda ishlangan 3 ta aromali shamdan iborat sovg'a to'plami.",
            "price_cashback": 50000,
            "stock_quantity": 20,
            "sort_order": 1,
        },
        {
            "name_ru": "Компактный диффузор",
            "name_uz": "Ixcham diffuzor",
            "description_ru": "Портативный USB-диффузор для дома и авто.",
            "description_uz": "Uy va avtomobil uchun portativ USB-diffuzor.",
            "price_cashback": 100000,
            "stock_quantity": 10,
            "sort_order": 2,
        },
        {
            "name_ru": "Премиум аромадиффузор",
            "name_uz": "Premium aromadiffuzor",
            "description_ru": "Ультразвуковой диффузор премиум-класса с LED-подсветкой.",
            "description_uz": "LED yoritgichli premium ultratovushli diffuzor.",
            "price_cashback": 200000,
            "stock_quantity": None,
            "sort_order": 3,
        },
    ]
    for g in gifts:
        await conn.execute(
            """
            INSERT INTO gifts (name_ru, name_uz, description_ru, description_uz,
                               price_cashback, stock_quantity, sort_order, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE)
            """,
            g["name_ru"], g["name_uz"], g["description_ru"], g["description_uz"],
            g["price_cashback"], g["stock_quantity"], g["sort_order"],
        )
    print(f"Добавлено подарков: {len(gifts)}")

    # ---------- Диффузоры (каталог) ----------
    diffusers = [
        {
            "name_ru": "Scenti Air 300",
            "name_uz": "Scenti Air 300",
            "description_ru": "Ультразвуковой аромадиффузор для помещений до 30 м².",
            "description_uz": "30 m² gacha xonalar uchun ultratovushli aromadiffuzor.",
            "type": "device",
            "tag_ru": "Ультразвуковой",
            "tag_uz": "Ultratovushli",
            "specs": {"volume": "300мл", "area": "30м²", "power": "12W"},
            "features": ["Таймер", "USB", "LED-подсветка", "Автоотключение"],
            "sort_order": 1,
        },
        {
            "name_ru": "Scenti Pro 500",
            "name_uz": "Scenti Pro 500",
            "description_ru": "Профессиональный диффузор с большим резервуаром для офисов.",
            "description_uz": "Ofislar uchun katta rezervuarli professional diffuzor.",
            "type": "device",
            "tag_ru": "Профессиональный",
            "tag_uz": "Professional",
            "specs": {"volume": "500мл", "area": "60м²", "power": "20W"},
            "features": ["Таймер 12ч", "Bluetooth", "Сенсорное управление"],
            "sort_order": 2,
        },
        {
            "name_ru": "Цитрусовый бриз",
            "name_uz": "Sitrus shabadasi",
            "description_ru": "Освежающий аромат с нотами апельсина, лимона и грейпфрута.",
            "description_uz": "Apelsin, limon va greypfrut notalari bilan tetiklantiruvchi hid.",
            "type": "aroma",
            "tag_ru": "Цитрус",
            "tag_uz": "Sitrus",
            "specs": {"volume": "50мл", "intensity": "средняя"},
            "features": ["Натуральные масла", "Долгое звучание"],
            "sort_order": 3,
        },
    ]
    for d in diffusers:
        await conn.execute(
            """
            INSERT INTO diffusers (name_ru, name_uz, description_ru, description_uz, type,
                                   tag_ru, tag_uz, specs, features, sort_order, is_active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE)
            """,
            d["name_ru"], d["name_uz"], d["description_ru"], d["description_uz"], d["type"],
            d["tag_ru"], d["tag_uz"], json.dumps(d["specs"]), d["features"], d["sort_order"],
        )
    print(f"Добавлено диффузоров: {len(diffusers)}")

    # ---------- Тестовый агент ----------
    existing = await conn.fetchval("SELECT id FROM agents WHERE username = $1", "agent1")
    if existing:
        await conn.execute(
            "UPDATE agents SET password_hash = $1, is_active = TRUE WHERE username = 'agent1'",
            hash_password("agent123"),
        )
        print("Агент agent1 уже существует — пароль обновлён")
    else:
        await conn.execute(
            """
            INSERT INTO agents (name, phone, username, password_hash, is_active)
            VALUES ($1,$2,$3,$4,TRUE)
            """,
            "Тестовый Агент", "+998901234567", "agent1", hash_password("agent123"),
        )
        print("Добавлен агент: agent1 / agent123")

    await conn.close()
    print("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
