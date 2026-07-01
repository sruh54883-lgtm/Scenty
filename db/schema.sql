-- Scenti Loyalty System — PostgreSQL schema (idempotent)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS regions (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(100) NOT NULL,
    name_uz VARCHAR(100) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS districts (
    id SERIAL PRIMARY KEY,
    region_id INT NOT NULL REFERENCES regions(id),
    name_ru VARCHAR(100) NOT NULL,
    name_uz VARCHAR(100) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    business_name VARCHAR(255),
    phone VARCHAR(30) UNIQUE,
    region_id INT REFERENCES regions(id),
    district_id INT REFERENCES districts(id),
    cashback_balance BIGINT NOT NULL DEFAULT 0,
    language VARCHAR(5) NOT NULL DEFAULT 'ru',
    privacy_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(30) NOT NULL,
    password_hash TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_districts (
    agent_id INT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    district_id INT NOT NULL REFERENCES districts(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, district_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    agent_id INT REFERENCES agents(id),
    amount BIGINT NOT NULL,
    cashback_amount BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    confirmed_at TIMESTAMPTZ,
    confirmed_by INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gifts (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(255) NOT NULL,
    name_uz VARCHAR(255) NOT NULL,
    description_ru TEXT NOT NULL DEFAULT '',
    description_uz TEXT NOT NULL DEFAULT '',
    image_url VARCHAR(500) NOT NULL DEFAULT '',
    price_cashback BIGINT NOT NULL,
    stock_quantity INT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gift_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    gift_id INT NOT NULL REFERENCES gifts(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    admin_notes TEXT NOT NULL DEFAULT '',
    price_paid BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cashback_spends (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    amount BIGINT NOT NULL,
    gift_request_id BIGINT REFERENCES gift_requests(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS diffusers (
    id SERIAL PRIMARY KEY,
    name_ru VARCHAR(255) NOT NULL,
    name_uz VARCHAR(255) NOT NULL,
    description_ru TEXT NOT NULL DEFAULT '',
    description_uz TEXT NOT NULL DEFAULT '',
    type VARCHAR(20) NOT NULL,
    tag_ru VARCHAR(100),
    tag_uz VARCHAR(100),
    image_url VARCHAR(500) NOT NULL DEFAULT '',
    specs JSONB NOT NULL DEFAULT '{}',
    features TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS privacy_policy (
    id SERIAL PRIMARY KEY,
    content_ru TEXT NOT NULL DEFAULT '',
    content_uz TEXT NOT NULL DEFAULT '',
    file_url VARCHAR(500) NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    message_ru TEXT NOT NULL,
    message_uz TEXT NOT NULL,
    target VARCHAR(20) NOT NULL DEFAULT 'all',
    region_id INT REFERENCES regions(id),
    sent_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monthly_reminder (
    id SERIAL PRIMARY KEY,
    message_ru TEXT NOT NULL DEFAULT '',
    message_uz TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_superadmin BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    admin_id INT REFERENCES admin_users(id),
    agent_id INT REFERENCES agents(id),
    action VARCHAR(100) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    ip_address VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_agent_id ON transactions(agent_id);
CREATE INDEX IF NOT EXISTS idx_gift_requests_user_id ON gift_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_gift_requests_status ON gift_requests(status);
CREATE INDEX IF NOT EXISTS idx_cashback_spends_user_id ON cashback_spends(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at DESC);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_users_upd') THEN
    CREATE TRIGGER trg_users_upd BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_transactions_upd') THEN
    CREATE TRIGGER trg_transactions_upd BEFORE UPDATE ON transactions FOR EACH ROW EXECUTE FUNCTION update_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_gift_requests_upd') THEN
    CREATE TRIGGER trg_gift_requests_upd BEFORE UPDATE ON gift_requests FOR EACH ROW EXECUTE FUNCTION update_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_gifts_upd') THEN
    CREATE TRIGGER trg_gifts_upd BEFORE UPDATE ON gifts FOR EACH ROW EXECUTE FUNCTION update_updated_at();
  END IF;
END $$;

-- ============================================================ MIGRATIONS
-- Отложенная рассылка + фильтр по языку для broadcasts.
-- Идемпотентно (IF NOT EXISTS). Существующие строки трактуются как уже
-- отправленные (is_sent=TRUE) — defaults берут это на себя.
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS lang_filter VARCHAR(5);             -- 'ru' | 'uz' | NULL = все
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS is_sent BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS parse_mode VARCHAR(10) NOT NULL DEFAULT 'HTML';
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS image_url VARCHAR(500) NOT NULL DEFAULT '';

-- Индекс для воркера отложенных рассылок (выборка ожидающих по времени).
CREATE INDEX IF NOT EXISTS idx_broadcasts_pending
  ON broadcasts(scheduled_at) WHERE is_sent = FALSE;

-- Обновление названий регионов: добавляем "область", "Город", "Республика"
UPDATE regions SET name_ru = 'Андижанская область'       WHERE code = 'AND' AND name_ru = 'Андижанская';
UPDATE regions SET name_ru = 'Бухарская область'          WHERE code = 'BUX' AND name_ru = 'Бухарская';
UPDATE regions SET name_ru = 'Джизакская область'         WHERE code = 'JIZ' AND name_ru = 'Джизакская';
UPDATE regions SET name_ru = 'Кашкадарьинская область'    WHERE code = 'QAS' AND name_ru = 'Кашкадарьинская';
UPDATE regions SET name_ru = 'Навоийская область'         WHERE code = 'NAV' AND name_ru = 'Навоийская';
UPDATE regions SET name_ru = 'Наманганская область'       WHERE code = 'NAM' AND name_ru = 'Наманганская';
UPDATE regions SET name_ru = 'Самаркандская область'      WHERE code = 'SAM' AND name_ru = 'Самаркандская';
UPDATE regions SET name_ru = 'Сурхандарьинская область'   WHERE code = 'SUR' AND name_ru = 'Сурхандарьинская';
UPDATE regions SET name_ru = 'Сырдарьинская область'      WHERE code = 'SIR' AND name_ru = 'Сырдарьинская';
UPDATE regions SET name_ru = 'Ташкентская область'        WHERE code = 'TSH' AND name_ru = 'Ташкентская';
UPDATE regions SET name_ru = 'Ферганская область'         WHERE code = 'FAR' AND name_ru = 'Ферганская';
UPDATE regions SET name_ru = 'Хорезмская область'         WHERE code = 'XOR' AND name_ru = 'Хорезмская';
UPDATE regions SET name_ru = 'Республика Каракалпакстан'  WHERE code = 'KAR' AND name_ru = 'Каракалпакстан';
UPDATE regions SET name_ru = 'Город Ташкент'              WHERE code = 'TSH-S' AND name_ru = 'Ташкент (город)';

-- Seed: регионы Узбекистана
INSERT INTO regions (name_ru, name_uz, code) VALUES
('Андижанская область',      'Andijon viloyati',        'AND'),
('Бухарская область',        'Buxoro viloyati',         'BUX'),
('Джизакская область',       'Jizzax viloyati',         'JIZ'),
('Кашкадарьинская область',  'Qashqadaryo viloyati',    'QAS'),
('Навоийская область',       'Navoiy viloyati',         'NAV'),
('Наманганская область',     'Namangan viloyati',       'NAM'),
('Самаркандская область',    'Samarqand viloyati',      'SAM'),
('Сурхандарьинская область', 'Surxondaryo viloyati',    'SUR'),
('Сырдарьинская область',    'Sirdaryo viloyati',       'SIR'),
('Ташкентская область',      'Toshkent viloyati',       'TSH'),
('Ферганская область',       'Farg''ona viloyati',      'FAR'),
('Хорезмская область',       'Xorazm viloyati',         'XOR'),
('Республика Каракалпакстан','Qoraqalpog''iston Respublikasi', 'KAR'),
('Город Ташкент',            'Toshkent shahri',         'TSH-S')
ON CONFLICT (code) DO NOTHING;

-- Seed: районы Узбекистана
INSERT INTO districts (region_id, name_ru, name_uz, code)
SELECT r.id, d.name_ru, d.name_uz, d.code FROM regions r JOIN (VALUES
  -- Андижанская
  ('AND','Андижан (город)',    'Andijon shahri',   'AND-01'),
  ('AND','Асака',              'Asaka',            'AND-02'),
  ('AND','Балиқчи',            'Baliqchi',         'AND-03'),
  ('AND','Боз',                'Boz',              'AND-04'),
  ('AND','Булoqboshi',         'Buloqboshi',       'AND-05'),
  ('AND','Джалакудук',         'Jalaquduq',        'AND-06'),
  ('AND','Избоскан',           'Izboskan',         'AND-07'),
  ('AND','Кургантепа',         'Qo''rg''ontepa',   'AND-08'),
  ('AND','Мархамат',           'Marhamat',         'AND-09'),
  ('AND','Олтинкол',           'Oltinkol',         'AND-10'),
  ('AND','Пахтаобод',          'Paxtaobod',        'AND-11'),
  ('AND','Улугнор',            'Ulug''nor',        'AND-12'),
  ('AND','Ходжаобод',          'Xo''jaobod',       'AND-13'),
  ('AND','Шахрихан',           'Shahrixon',        'AND-14'),
  -- Бухарская
  ('BUX','Бухара (город)',     'Buxoro shahri',    'BUX-01'),
  ('BUX','Гиждуван',           'Gijduvon',         'BUX-02'),
  ('BUX','Жондор',             'Jondor',           'BUX-03'),
  ('BUX','Коган',              'Kogon',            'BUX-04'),
  ('BUX','Олот',               'Olot',             'BUX-05'),
  ('BUX','Пешку',              'Peshku',           'BUX-06'),
  ('BUX','Каракуль',           'Qorovulbozor',     'BUX-07'),
  ('BUX','Ромитан',            'Romitan',          'BUX-08'),
  ('BUX','Шафиркан',           'Shofirkon',        'BUX-09'),
  ('BUX','Вабкент',            'Vobkent',          'BUX-10'),
  ('BUX','Қарагул',            'Qaragul',          'BUX-11'),
  -- Джизакская
  ('JIZ','Джизак (город)',     'Jizzax shahri',    'JIZ-01'),
  ('JIZ','Арнасай',            'Arnasoy',          'JIZ-02'),
  ('JIZ','Бахмал',             'Baxmal',           'JIZ-03'),
  ('JIZ','Дусть',              'Do''stlik',        'JIZ-04'),
  ('JIZ','Фориш',              'Forish',           'JIZ-05'),
  ('JIZ','Галляарал',          'G''allaorol',      'JIZ-06'),
  ('JIZ','Пахтакор',           'Paxtakor',         'JIZ-07'),
  ('JIZ','Янгиабад',           'Yangiabad',        'JIZ-08'),
  ('JIZ','Зафарабад',          'Zafarobod',        'JIZ-09'),
  ('JIZ','Зомин',              'Zomin',            'JIZ-10'),
  ('JIZ','Зарбдор',            'Zarbdor',          'JIZ-11'),
  ('JIZ','Мирзачуль',          'Mirzacho''l',      'JIZ-12'),
  -- Кашкадарьинская
  ('QAS','Карши (город)',      'Qarshi shahri',    'QAS-01'),
  ('QAS','Чирокчи',            'Chiroqchi',        'QAS-02'),
  ('QAS','Дехканабад',         'Dehqonobod',       'QAS-03'),
  ('QAS','Гузар',              'G''uzor',          'QAS-04'),
  ('QAS','Камаши',             'Kamashi',          'QAS-05'),
  ('QAS','Касби',              'Kasbi',            'QAS-06'),
  ('QAS','Китаб',              'Kitob',            'QAS-07'),
  ('QAS','Косон',              'Koson',            'QAS-08'),
  ('QAS','Миришкор',           'Mirishkor',        'QAS-09'),
  ('QAS','Мубарак',            'Muborak',          'QAS-10'),
  ('QAS','Нишан',              'Nishon',           'QAS-11'),
  ('QAS','Шахрисабз',          'Shahrisabz',       'QAS-12'),
  ('QAS','Яккабаг',            'Yakkabog''',       'QAS-13'),
  -- Навоийская
  ('NAV','Навои (город)',      'Navoiy shahri',    'NAV-01'),
  ('NAV','Кармана',            'Karmana',          'NAV-02'),
  ('NAV','Конимех',            'Konimex',          'NAV-03'),
  ('NAV','Навбахор',           'Navbahor',         'NAV-04'),
  ('NAV','Нурата',             'Nurota',           'NAV-05'),
  ('NAV','Кизилтепа',          'Qiziltepa',        'NAV-06'),
  ('NAV','Томди',              'Tomdi',            'NAV-07'),
  ('NAV','Учкудук',            'Uchquduq',         'NAV-08'),
  ('NAV','Хатирчи',            'Xatirchi',         'NAV-09'),
  -- Наманганская
  ('NAM','Наманган (город)',   'Namangan shahri',  'NAM-01'),
  ('NAM','Чартак',             'Chortoq',          'NAM-02'),
  ('NAM','Чуст',               'Chust',            'NAM-03'),
  ('NAM','Касансай',           'Kosonsoy',         'NAM-04'),
  ('NAM','Мингбулак',          'Mingbuloq',        'NAM-05'),
  ('NAM','Норин',              'Norin',            'NAM-06'),
  ('NAM','Поп',                'Pop',              'NAM-07'),
  ('NAM','Туракурган',         'To''raqo''rg''on', 'NAM-08'),
  ('NAM','Учкурган',           'Uchqo''rg''on',    'NAM-09'),
  ('NAM','Янгикурган',         'Yangiqo''rg''on',  'NAM-10'),
  ('NAM','Давлатабад',         'Davlatobod',       'NAM-11'),
  -- Самаркандская
  ('SAM','Самарканд (город)',  'Samarqand shahri', 'SAM-01'),
  ('SAM','Булунгур',           'Bulung''ur',       'SAM-02'),
  ('SAM','Иштихан',            'Ishtixon',         'SAM-03'),
  ('SAM','Жомбой',             'Jomboy',           'SAM-04'),
  ('SAM','Каттакурган',        'Kattaqo''rg''on',  'SAM-05'),
  ('SAM','Нарпай',             'Narpay',           'SAM-06'),
  ('SAM','Нурабад',            'Nurobod',          'SAM-07'),
  ('SAM','Окдарья',            'Oqdaryo',          'SAM-08'),
  ('SAM','Пастдаргом',         'Pastdarg''om',     'SAM-09'),
  ('SAM','Пайарик',            'Payariq',          'SAM-10'),
  ('SAM','Пахтачи',            'Paxtachi',         'SAM-11'),
  ('SAM','Кушрабат',           'Qo''shrabot',      'SAM-12'),
  ('SAM','Тайлак',             'Tayloq',           'SAM-13'),
  ('SAM','Ургут',              'Urgut',            'SAM-14'),
  -- Сурхандарьинская
  ('SUR','Термез (город)',     'Termez shahri',    'SUR-01'),
  ('SUR','Ангор',              'Angor',            'SUR-02'),
  ('SUR','Бандихан',           'Bandixon',         'SUR-03'),
  ('SUR','Байсун',             'Boysun',           'SUR-04'),
  ('SUR','Денау',              'Denov',            'SUR-05'),
  ('SUR','Жаркурган',          'Jarqo''rg''on',    'SUR-06'),
  ('SUR','Музрабад',           'Muzrabot',         'SUR-07'),
  ('SUR','Алтинсай',           'Oltinsoy',         'SUR-08'),
  ('SUR','Кизирик',            'Qiziriq',          'SUR-09'),
  ('SUR','Кумкурган',          'Qumqo''rg''on',    'SUR-10'),
  ('SUR','Сариасия',           'Sariosiyo',        'SUR-11'),
  ('SUR','Шерабад',            'Sherobod',         'SUR-12'),
  ('SUR','Шурчи',              'Sho''rchi',        'SUR-13'),
  ('SUR','Узун',               'Uzun',             'SUR-14'),
  -- Сырдарьинская
  ('SIR','Гулистан (город)',   'Guliston shahri',  'SIR-01'),
  ('SIR','Баяут',              'Boyovut',          'SIR-02'),
  ('SIR','Мирзаобад',          'Mirzaobod',        'SIR-03'),
  ('SIR','Окалтин',            'Oqoltin',          'SIR-04'),
  ('SIR','Сардоба',            'Sardoba',          'SIR-05'),
  ('SIR','Сайхунабад',         'Sayxunobod',       'SIR-06'),
  ('SIR','Сырдарья',           'Sirdaryo',         'SIR-07'),
  ('SIR','Ховос',              'Xovos',            'SIR-08'),
  ('SIR','Янгиер',             'Yangiyer',         'SIR-09'),
  -- Ташкентская
  ('TSH','Бекабад',            'Bekabad',          'TSH-01'),
  ('TSH','Бука',               'Bo''ka',           'TSH-02'),
  ('TSH','Бостанлык',          'Bostonliq',        'TSH-03'),
  ('TSH','Чиноз',              'Chinoz',           'TSH-04'),
  ('TSH','Чирчик',             'Chirchiq',         'TSH-05'),
  ('TSH','Ахангаран',          'Ohangaron',        'TSH-06'),
  ('TSH','Алмалык',            'Olmaliq',          'TSH-07'),
  ('TSH','Паркент',            'Parkent',          'TSH-08'),
  ('TSH','Пискент',            'Piskent',          'TSH-09'),
  ('TSH','Кибрай',             'Qibray',           'TSH-10'),
  ('TSH','Ташкентский район',  'Toshkent tumani',  'TSH-11'),
  ('TSH','Юкоричирчик',        'Yuqorichirchiq',   'TSH-12'),
  ('TSH','Зангиата',           'Zangiota',         'TSH-13'),
  ('TSH','Нурафшон',           'Nurafshon',        'TSH-14'),
  ('TSH','Янгийул',            'Yangiyo''l',       'TSH-15'),
  -- Ферганская
  ('FAR','Фергана (город)',    'Farg''ona shahri', 'FAR-01'),
  ('FAR','Бешариқ',            'Beshariq',         'FAR-02'),
  ('FAR','Богдад',             'Bog''dod',         'FAR-03'),
  ('FAR','Бувайда',            'Buvayda',          'FAR-04'),
  ('FAR','Дангара',            'Dangara',          'FAR-05'),
  ('FAR','Фуркат',             'Furqat',           'FAR-06'),
  ('FAR','Коканд',             'Qo''qon',          'FAR-07'),
  ('FAR','Маргилан',           'Marg''ilon',       'FAR-08'),
  ('FAR','Алтиарык',           'Oltiariq',         'FAR-09'),
  ('FAR','Риштан',             'Rishton',          'FAR-10'),
  ('FAR','Узбекистан р-н',     'O''zbekiston',     'FAR-11'),
  ('FAR','Кува',               'Quva',             'FAR-12'),
  ('FAR','Сух',                'So''x',            'FAR-13'),
  ('FAR','Ташлак',             'Toshloq',          'FAR-14'),
  ('FAR','Учкупрюк',           'Uchko''prik',      'FAR-15'),
  ('FAR','Яйпан',              'Yaypan',           'FAR-16'),
  -- Хорезмская
  ('XOR','Ургенч (город)',     'Urganch shahri',   'XOR-01'),
  ('XOR','Багат',              'Bog''ot',          'XOR-02'),
  ('XOR','Гурлан',             'Gurlan',           'XOR-03'),
  ('XOR','Хазарасп',           'Hazorasp',         'XOR-04'),
  ('XOR','Хива',               'Xiva',             'XOR-05'),
  ('XOR','Кушкупир',           'Qo''shko''pir',    'XOR-06'),
  ('XOR','Шават',              'Shovot',           'XOR-07'),
  ('XOR','Туpraqkal',          'Tuproqqal''a',     'XOR-08'),
  ('XOR','Ургенч р-н',         'Urganch tumani',   'XOR-09'),
  ('XOR','Янгибазар',          'Yangibozor',       'XOR-10'),
  ('XOR','Янгиарык',           'Yangiariq',        'XOR-11'),
  -- Каракалпакстан
  ('KAR','Нукус (город)',      'Nukus shahri',     'KAR-01'),
  ('KAR','Амударья',           'Amudaryo',         'KAR-02'),
  ('KAR','Беруний',            'Beruniy',          'KAR-03'),
  ('KAR','Чимбай',             'Chimboy',          'KAR-04'),
  ('KAR','Элликкала',          'Ellikkala',        'KAR-05'),
  ('KAR','Кегейли',            'Kegeyli',          'KAR-06'),
  ('KAR','Муйнак',             'Mo''ynoq',         'KAR-07'),
  ('KAR','Нукус р-н',          'Nukus tumani',     'KAR-08'),
  ('KAR','Канликуль',          'Qanliko''l',       'KAR-09'),
  ('KAR','Кaраузяк',           'Qorao''zak',       'KAR-10'),
  ('KAR','Шуманай',            'Shumanay',         'KAR-11'),
  ('KAR','Тахтакупир',         'Taxtako''pir',     'KAR-12'),
  ('KAR','Туртуль',            'To''rtko''l',      'KAR-13'),
  ('KAR','Ходжейли',           'Xo''jayli',        'KAR-14'),
  -- Ташкент (город)
  ('TSH-S','Бектемир',         'Bektemir',         'TSHS-01'),
  ('TSH-S','Чиланзар',         'Chilonzor',        'TSHS-02'),
  ('TSH-S','Мирзо Улугбек',    'Mirzo Ulug''bek',  'TSHS-03'),
  ('TSH-S','Мирабад',          'Mirobod',          'TSHS-04'),
  ('TSH-S','Олмазар',          'Olmazor',          'TSHS-05'),
  ('TSH-S','Сергели',          'Sergeli',          'TSHS-06'),
  ('TSH-S','Шайхантахур',      'Shayxontohur',     'TSHS-07'),
  ('TSH-S','Учтепа',           'Uchtepa',          'TSHS-08'),
  ('TSH-S','Яккасарай',        'Yakkasaroy',       'TSHS-09'),
  ('TSH-S','Яшнабад',          'Yashnobod',        'TSHS-10'),
  ('TSH-S','Юнусабад',         'Yunusobod',        'TSHS-11')
) AS d(rcode, name_ru, name_uz, code) ON r.code = d.rcode
ON CONFLICT (code) DO NOTHING;

-- admin_users seed: пароль устанавливается через migrate.py из ADMIN_SECRET env var
-- Здесь только заглушка; migrate.py перезапишет password_hash через bcrypt
INSERT INTO admin_users (username, password_hash, is_superadmin)
VALUES ('admin', 'PLACEHOLDER_SET_BY_MIGRATE', TRUE)
ON CONFLICT (username) DO NOTHING;

-- Seed: политика конфиденциальности
INSERT INTO privacy_policy (content_ru, content_uz)
SELECT
  'Scenti уважает вашу конфиденциальность. Мы собираем только данные, необходимые для работы программы лояльности: Telegram ID, имя, телефон, название бизнеса и историю кешбэка. Данные не передаются третьим лицам.',
  'Scenti sizning maxfiyligingizni hurmat qiladi. Biz faqat sodiqlik dasturi uchun zarur ma''lumotlarni to''playmiz: Telegram ID, ism, telefon, biznes nomi va keshbek tarixi. Ma''lumotlar uchinchi shaxslarga berilmaydi.'
WHERE NOT EXISTS (SELECT 1 FROM privacy_policy);

-- Seed: ежемесячное напоминание
INSERT INTO monthly_reminder (message_ru, message_uz)
SELECT
  '👋 Привет! Напоминаем, что у вас есть кешбэк в приложении Scenti. Используйте его при следующей покупке ароматов!',
  '👋 Salom! Scenti ilovasida keshbegingiz borligini eslatamiz. Keyingi xaridda undan foydalaning!'
WHERE NOT EXISTS (SELECT 1 FROM monthly_reminder);

-- Настройки приложения (контакты поддержки и др.)
CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

INSERT INTO app_settings (key, value) VALUES
  ('contact_phone', '+998773831111'),
  ('contact_tg', 'Scentioffice1'),
  ('contact_phone_display', '+998 77 383 11 11')
ON CONFLICT (key) DO NOTHING;
