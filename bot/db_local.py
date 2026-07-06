"""SQLite database adapter for local development."""
import aiosqlite
import os
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "../scenti_local.db")

_conn: Optional[aiosqlite.Connection] = None


async def get_conn() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA foreign_keys=ON")
        await init_schema()
    return _conn


async def init_schema():
    conn = await get_conn()
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_ru TEXT NOT NULL,
            name_uz TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id INTEGER NOT NULL REFERENCES regions(id),
            name_ru TEXT NOT NULL,
            name_uz TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            business_name TEXT,
            phone TEXT,
            region_id INTEGER REFERENCES regions(id),
            district_id INTEGER REFERENCES districts(id),
            cashback_balance INTEGER NOT NULL DEFAULT 0,
            language TEXT NOT NULL DEFAULT 'ru',
            privacy_accepted INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS privacy_policy (
            id INTEGER PRIMARY KEY,
            content_ru TEXT NOT NULL DEFAULT '',
            content_uz TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    await conn.commit()

    # Seed регионы
    cur = await conn.execute("SELECT COUNT(*) FROM regions")
    row = await cur.fetchone()
    if row[0] == 0:
        regions = [
            ("Андижанская", "Andijon", "AND"),
            ("Бухарская", "Buxoro", "BUX"),
            ("Джизакская", "Jizzax", "JIZ"),
            ("Кашкадарьинская", "Qashqadaryo", "QAS"),
            ("Навоийская", "Navoiy", "NAV"),
            ("Наманганская", "Namangan", "NAM"),
            ("Самаркандская", "Samarqand", "SAM"),
            ("Сурхандарьинская", "Surxondaryo", "SUR"),
            ("Сырдарьинская", "Sirdaryo", "SIR"),
            ("Ташкентская", "Toshkent viloyati", "TSH"),
            ("Ферганская", "Farg'ona", "FAR"),
            ("Хорезмская", "Xorazm", "XOR"),
            ("Каракалпакстан", "Qoraqalpog'iston", "KAR"),
            ("Ташкент (город)", "Toshkent (shahar)", "TSH-S"),
        ]
        await conn.executemany(
            "INSERT INTO regions (name_ru, name_uz, code) VALUES (?, ?, ?)", regions
        )

        # Несколько районов для каждого региона (упрощённо)
        districts = [
            (1, "Андижан (город)", "Andijon shahri", "AND-1"),
            (1, "Балиқчи", "Baliqchi", "AND-2"),
            (2, "Бухара (город)", "Buxoro shahri", "BUX-1"),
            (2, "Каган", "Kogon", "BUX-2"),
            (3, "Джизак (город)", "Jizzax shahri", "JIZ-1"),
            (4, "Карши (город)", "Qarshi shahri", "QAS-1"),
            (5, "Навои (город)", "Navoiy shahri", "NAV-1"),
            (6, "Наманган (город)", "Namangan shahri", "NAM-1"),
            (7, "Самарканд (город)", "Samarqand shahri", "SAM-1"),
            (7, "Пенджикент", "Panjakent", "SAM-2"),
            (8, "Термез (город)", "Termiz shahri", "SUR-1"),
            (9, "Гулистан", "Guliston", "SIR-1"),
            (10, "Ташкент р-н", "Toshkent tumani", "TSH-1"),
            (10, "Алмалык", "Olmaliq", "TSH-2"),
            (11, "Фергана (город)", "Farg'ona shahri", "FAR-1"),
            (11, "Коканд", "Qo'qon", "FAR-2"),
            (12, "Ургенч (город)", "Urganch shahri", "XOR-1"),
            (13, "Нукус (город)", "Nukus shahri", "KAR-1"),
            (14, "Юнусабад", "Yunusobod", "TSH-S-1"),
            (14, "Мирзо-Улугбек", "Mirzo Ulug'bek", "TSH-S-2"),
            (14, "Чиланзар", "Chilonzor", "TSH-S-3"),
            (14, "Яккасарай", "Yakkasaroy", "TSH-S-4"),
            (14, "Алмазар", "Olmazor", "TSH-S-5"),
            (14, "Бектемир", "Bektemir", "TSH-S-6"),
            (14, "Яшнабад", "Yashnobod", "TSH-S-7"),
            (14, "Сергели", "Sergeli", "TSH-S-8"),
            (14, "Учтепа", "Uchtepa", "TSH-S-9"),
            (14, "Шайхантахур", "Shayxontohur", "TSH-S-10"),
        ]
        await conn.executemany(
            "INSERT INTO districts (region_id, name_ru, name_uz, code) VALUES (?, ?, ?, ?)",
            districts,
        )

    # Seed политика
    cur = await conn.execute("SELECT COUNT(*) FROM privacy_policy")
    row = await cur.fetchone()
    if row[0] == 0:
        await conn.execute(
            "INSERT INTO privacy_policy (content_ru, content_uz) VALUES (?, ?)",
            (
                "Scenti уважает вашу конфиденциальность. Мы собираем только данные, необходимые для работы программы лояльности: Telegram ID, имя, телефон, название бизнеса и историю начислений кешбэка. Данные не передаются третьим лицам и используются исключительно для начисления кешбэка и обработки заявок.",
                "Scenti sizning maxfiyligingizni hurmat qiladi. Biz faqat sodiqlik dasturi uchun zarur ma'lumotlarni to'playmiz: Telegram ID, ism, telefon, biznes nomi va keshbek tarixi. Ma'lumotlar uchinchi shaxslarga berilmaydi.",
            ),
        )

    await conn.commit()


async def get_user(telegram_id: int):
    conn = await get_conn()
    cur = await conn.execute(
        """SELECT u.*, r.name_ru as region_name, d.name_ru as district_name
           FROM users u
           LEFT JOIN regions r ON u.region_id = r.id
           LEFT JOIN districts d ON u.district_id = d.id
           WHERE u.telegram_id = ?""",
        (telegram_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_user(telegram_id: int, **fields):
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
    )
    existing = await cur.fetchone()
    if existing:
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [telegram_id]
            await conn.execute(
                f"UPDATE users SET {sets}, updated_at = datetime('now') WHERE telegram_id = ?",
                vals,
            )
    else:
        fields["telegram_id"] = telegram_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        await conn.execute(
            f"INSERT INTO users ({cols}) VALUES ({placeholders})", list(fields.values())
        )
    await conn.commit()


async def get_privacy_policy(lang: str = "ru") -> str:
    conn = await get_conn()
    field = "content_ru" if lang == "ru" else "content_uz"
    cur = await conn.execute(f"SELECT {field} FROM privacy_policy LIMIT 1")
    row = await cur.fetchone()
    return row[0] if row else "Политика конфиденциальности."


async def get_regions():
    conn = await get_conn()
    cur = await conn.execute("SELECT id, name_ru, name_uz FROM regions ORDER BY id")
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_districts(region_id: int):
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, name_ru, name_uz FROM districts WHERE region_id = ? ORDER BY id",
        (region_id,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def close():
    global _conn
    if _conn:
        await _conn.close()
        _conn = None
