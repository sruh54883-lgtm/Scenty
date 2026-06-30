"""Добавляет диффузор S-100 в каталог."""
import asyncio, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))


async def main():
    import asyncpg
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL not set"); return
    ssl = "require" if "sslmode=require" in url else None
    conn = await asyncpg.connect(url, timeout=15, ssl=ssl)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO diffusers
              (name_ru, name_uz, description_ru, description_uz,
               type, tag_ru, tag_uz, image_url, sort_order)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            "S-100",
            "S-100",
            (
                "✅ Эффективное ароматизирование до 100 м²\n"
                "✅ 400 мл аромакапсула — спокойствие и комфорт на недели\n"
                "✅ Работает от электричества — надёжность без перебоев\n"
                "✅ Управление через мобильное приложение — всё в ваших руках"
            ),
            (
                "✅ 100 m² gacha samarali iforlantirish\n"
                "✅ 400 ml aroma kapsula — haftalab tinchlik va huzur\n"
                "✅ Elektrda ishlaydi — uzluksiz barqarorlik\n"
                "✅ Mobil ilova orqali boshqaruv — barchasi qo'lingizda"
            ),
            "device",
            "До 100 кв.м",
            "100 m² gacha",
            "",   # image_url — загрузите фото через админку после деплоя
            20,
        )
        if row:
            print(f"✅ S-100 добавлен, id={row['id']}")
        else:
            print("ℹ️  S-100 уже есть в каталоге")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
