-- Scenti Loyalty System — PostgreSQL schema

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Регионы Узбекистана
CREATE TABLE regions (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(100) NOT NULL,
    name_uz VARCHAR(100) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL
);

-- Районы
CREATE TABLE districts (
    id SERIAL PRIMARY KEY,
    region_id INT NOT NULL REFERENCES regions(id),
    name_ru VARCHAR(100) NOT NULL,
    name_uz VARCHAR(100) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL
);

-- Клиенты (Telegram пользователи)
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    business_name VARCHAR(255),
    phone VARCHAR(30),
    region_id INT REFERENCES regions(id),
    district_id INT REFERENCES districts(id),
    cashback_balance BIGINT NOT NULL DEFAULT 0, -- в сумах
    language VARCHAR(5) NOT NULL DEFAULT 'ru',  -- 'ru' / 'uz'
    privacy_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Агенты (отдельные аккаунты, не клиенты)
CREATE TABLE agents (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(30) NOT NULL,
    password_hash TEXT,                 -- для веб-входа
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Районы агента (many-to-many)
CREATE TABLE agent_districts (
    agent_id INT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    district_id INT NOT NULL REFERENCES districts(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, district_id)
);

-- Транзакции покупок (агент вносит → супер-админ подтверждает)
CREATE TABLE transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    agent_id INT NOT NULL REFERENCES agents(id),
    amount BIGINT NOT NULL,              -- сумма покупки в сумах (то что агент получил на руки)
    cashback_amount BIGINT NOT NULL,     -- 10% от amount
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending / approved / rejected
    note TEXT NOT NULL DEFAULT '',
    confirmed_at TIMESTAMPTZ,
    confirmed_by INT,                    -- admin user id
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- История трат кешбэка (клиент списал кешбэк)
CREATE TABLE cashback_spends (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    amount BIGINT NOT NULL,             -- сколько потратил кешбэка
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Подарки (каталог для обмена на кешбэк)
CREATE TABLE gifts (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(255) NOT NULL,
    name_uz VARCHAR(255) NOT NULL,
    description_ru TEXT NOT NULL DEFAULT '',
    description_uz TEXT NOT NULL DEFAULT '',
    image_url VARCHAR(500) NOT NULL DEFAULT '',
    price_cashback BIGINT NOT NULL,     -- стоимость в сумах кешбэка
    stock_quantity INT,                  -- NULL = безлимит
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Заявки на получение подарка
CREATE TABLE gift_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    gift_id INT NOT NULL REFERENCES gifts(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending / approved / delivered / rejected
    admin_notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Каталог: диффузоры и ароматы
CREATE TABLE diffusers (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(255) NOT NULL,
    name_uz VARCHAR(255) NOT NULL,
    description_ru TEXT NOT NULL DEFAULT '',
    description_uz TEXT NOT NULL DEFAULT '',
    type VARCHAR(20) NOT NULL,          -- 'device' / 'aroma'
    tag_ru VARCHAR(100),                -- подзаголовок: "Ультразвуковой", "Цитрус" и т.д.
    tag_uz VARCHAR(100),
    image_url VARCHAR(500) NOT NULL DEFAULT '',
    specs JSONB NOT NULL DEFAULT '{}',  -- {volume: "300мл", area: "30м²"}
    features TEXT[] NOT NULL DEFAULT '{}', -- ["Таймер", "USB", "LED"]
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Политика конфиденциальности
CREATE TABLE privacy_policy (
    id SERIAL PRIMARY KEY,
    content_ru TEXT NOT NULL DEFAULT '',
    content_uz TEXT NOT NULL DEFAULT '',
    file_url VARCHAR(500) NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Рассылки
CREATE TABLE broadcasts (
    id SERIAL PRIMARY KEY,
    message_ru TEXT NOT NULL,
    message_uz TEXT NOT NULL,
    target VARCHAR(20) NOT NULL DEFAULT 'all', -- 'all' / 'region'
    region_id INT REFERENCES regions(id),
    sent_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ежемесячное напоминание (настройки)
CREATE TABLE monthly_reminder (
    id SERIAL PRIMARY KEY,
    message_ru TEXT NOT NULL DEFAULT '',
    message_uz TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Супер-Админы (веб-вход)
CREATE TABLE admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_superadmin BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Аудит лог действий администратора
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    admin_id INT REFERENCES admin_users(id),
    agent_id INT REFERENCES agents(id),
    action VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    ip_address VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индексы
CREATE INDEX idx_users_telegram_id ON users(telegram_id);
CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transactions_status ON transactions(status);
CREATE INDEX idx_transactions_agent_id ON transactions(agent_id);
CREATE INDEX idx_gift_requests_user_id ON gift_requests(user_id);
CREATE INDEX idx_gift_requests_status ON gift_requests(status);
CREATE INDEX idx_cashback_spends_user_id ON cashback_spends(user_id);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);

-- Функция автообновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_upd BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_transactions_upd BEFORE UPDATE ON transactions FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_gift_requests_upd BEFORE UPDATE ON gift_requests FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_gifts_upd BEFORE UPDATE ON gifts FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Seed: регионы Узбекистана
INSERT INTO regions (name_ru, name_uz, code) VALUES
('Андижанская', 'Andijon', 'AND'),('Бухарская', 'Buxoro', 'BUX'),('Джизакская', 'Jizzax', 'JIZ'),
('Кашкадарьинская', 'Qashqadaryo', 'QAS'),('Навоийская', 'Navoiy', 'NAV'),('Наманганская', 'Namangan', 'NAM'),
('Самаркандская', 'Samarqand', 'SAM'),('Сурхандарьинская', 'Surxondaryo', 'SUR'),('Сырдарьинская', 'Sirdaryo', 'SIR'),
('Ташкентская', 'Toshkent viloyati', 'TSH'),('Ферганская', 'Farg''ona', 'FAR'),('Хорезмская', 'Xorazm', 'XOR'),
('Каракалпакстан', 'Qoraqalpog''iston', 'KAR'),('Ташкент (город)', 'Toshkent (shahar)', 'TSH-S');

-- Seed: супер-админ (пароль: admin123)
INSERT INTO admin_users (username, password_hash) VALUES
('admin', crypt('admin123', gen_salt('bf')));

-- Seed: политика конфиденциальности
INSERT INTO privacy_policy (content_ru, content_uz) VALUES
('Scenti уважает вашу конфиденциальность. Мы собираем только данные, необходимые для работы программы лояльности: Telegram ID, имя, телефон, название бизнеса и историю кешбэка. Данные не передаются третьим лицам.',
 'Scenti sizning maxfiyligingizni hurmat qiladi. Biz faqat sodiqlik dasturi uchun zarur ma''lumotlarni to''playmiz: Telegram ID, ism, telefon, biznes nomi va keshbek tarixi. Ma''lumotlar uchinchi shaxslarga berilmaydi.');

-- Seed: ежемесячное напоминание
INSERT INTO monthly_reminder (message_ru, message_uz) VALUES
('👋 Привет! Напоминаем, что у вас есть кешбэк в приложении Scenti. Используйте его при следующей покупке ароматов!',
 '👋 Salom! Scenti ilovasida keshbegingiz borligini eslatamiz. Keyingi xaridda undan foydalaning!');
