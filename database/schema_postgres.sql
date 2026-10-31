-- ============================================
-- BANANA MANAGER — PostgreSQL Schema
-- Создать на Bothost перед импортом данных
-- ============================================

-- Хранилище JSON-данных экономики (вместо локальных .json-файлов).
-- Ключ = имя бывшего файла (balances.json, prizes.json и т.д.).
CREATE TABLE IF NOT EXISTS json_store (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TEXT NOT NULL
);

-- Экономика
CREATE TABLE IF NOT EXISTS businesses (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    business_key TEXT NOT NULL,
    branch_no INTEGER NOT NULL DEFAULT 1,
    upgrade_level INTEGER NOT NULL DEFAULT 0,
    products INTEGER NOT NULL DEFAULT 100,
    branch_balance INTEGER NOT NULL DEFAULT 0,
    talisman_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_collected_at TIMESTAMPTZ,
    regular_talisman_active INTEGER NOT NULL DEFAULT 0,
    talisman_bonus_percent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inventory (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_name TEXT NOT NULL,
    item_value INTEGER NOT NULL DEFAULT 0,
    acquired_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cases_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    case_type TEXT NOT NULL,
    reward_type TEXT NOT NULL,
    reward_value TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_cases (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    case_type TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auction_items (
    id SERIAL PRIMARY KEY,
    seller_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_name TEXT NOT NULL,
    item_value INTEGER NOT NULL DEFAULT 0,
    start_bid INTEGER NOT NULL,
    current_bid INTEGER NOT NULL,
    highest_bidder_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    ends_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_effects (
    user_id INTEGER PRIMARY KEY,
    prize_bonus_percent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS permanent_prize_effects (
    user_id INTEGER PRIMARY KEY,
    prize_bonus_percent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS permanent_business_effects (
    user_id INTEGER NOT NULL,
    business_key TEXT NOT NULL,
    branch_no INTEGER NOT NULL,
    bonus_percent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, business_key, branch_no)
);

-- Логи и статистика
CREATE TABLE IF NOT EXISTS exchange_logs (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    status TEXT,
    log TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscription_claims (
    user_id INTEGER PRIMARY KEY,
    claimed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_epoints (
    user_id BIGINT PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_daily_stats (
    user_id BIGINT NOT NULL,
    stat_date TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    gbanpl_count INTEGER NOT NULL DEFAULT 0,
    gban_count INTEGER NOT NULL DEFAULT 0,
    epoints_earned INTEGER NOT NULL DEFAULT 0,
    norm_epoints_awarded INTEGER NOT NULL DEFAULT 0,
    tech_forms_created INTEGER NOT NULL DEFAULT 0,
    tech_forms_approved INTEGER NOT NULL DEFAULT 0,
    tech_resets_executed INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, stat_date)
);

CREATE TABLE IF NOT EXISTS tech_action_forms (
    id SERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    form_kind TEXT NOT NULL,
    scope TEXT,
    reason TEXT,
    attachments_text TEXT,
    source_chat_id BIGINT,
    source_peer_id BIGINT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_stats (
    user_id INTEGER NOT NULL,
    stat_date TEXT NOT NULL,
    messages_count INTEGER NOT NULL DEFAULT 0,
    gbanpl_count INTEGER NOT NULL DEFAULT 0,
    gban_count INTEGER NOT NULL DEFAULT 0,
    epoints_earned INTEGER NOT NULL DEFAULT 0,
    norm_status TEXT NOT NULL DEFAULT 'not_completed',
    PRIMARY KEY (user_id, stat_date)
);

CREATE TABLE IF NOT EXISTS admin_norms (
    user_id INTEGER NOT NULL,
    norm_date TEXT NOT NULL,
    forms_submitted INTEGER NOT NULL DEFAULT 0,
    forms_accepted INTEGER NOT NULL DEFAULT 0,
    resets_done INTEGER NOT NULL DEFAULT 0,
    norm_status TEXT NOT NULL DEFAULT 'not_completed',
    epoints_reward INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, norm_date)
);

CREATE TABLE IF NOT EXISTS reset_requests (
    id SERIAL PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    target_user_id INTEGER NOT NULL,
    target_user_name TEXT,
    moderator_id INTEGER,
    moderator_name TEXT,
    property_type TEXT,
    amount TEXT,
    item_name TEXT,
    proof_photo TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    answered_by INTEGER,
    answered_at TIMESTAMPTZ,
    reject_reason TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS epoints_spends (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Banana Pass
CREATE TABLE IF NOT EXISTS banana_pass_users (
    user_id INTEGER PRIMARY KEY,
    season_id TEXT NOT NULL,
    premium_active INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS banana_pass_claims (
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    track TEXT NOT NULL,
    claimed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, season_id, level)
);

CREATE TABLE IF NOT EXISTS banana_pass_daily_quests (
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    quest_date TEXT NOT NULL,
    slot INTEGER NOT NULL,
    quest_id TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    target INTEGER NOT NULL,
    xp_reward INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_elite INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, season_id, quest_date, slot)
);

CREATE TABLE IF NOT EXISTS banana_pass_weekly_quests (
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    week_key TEXT NOT NULL,
    slot INTEGER NOT NULL,
    quest_id TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    target INTEGER NOT NULL,
    xp_reward INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, season_id, week_key, slot)
);

CREATE TABLE IF NOT EXISTS banana_pass_special_quests (
    user_id INTEGER NOT NULL,
    season_id TEXT NOT NULL,
    quest_date TEXT NOT NULL,
    slot INTEGER NOT NULL,
    quest_id TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    target INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    rewarded INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, season_id, quest_date, slot)
);

-- Настройки чатов
CREATE TABLE IF NOT EXISTS af_chat_settings (
    slot INTEGER PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    peer_id BIGINT,
    setter_id BIGINT,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS af_config (
    id SERIAL PRIMARY KEY,
    chat_id INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS af_tech_config (
    id SERIAL PRIMARY KEY,
    chat_id INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tech_chat_settings (
    chat_id BIGINT PRIMARY KEY,
    enabled BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tech_permissions (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    level BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS modified_chats (
    chat_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS games_blacklist (
    user_id BIGINT PRIMARY KEY,
    moderator_id BIGINT,
    reason_gban TEXT,
    datetime_globalban TEXT
);

-- Серверные настройки
CREATE TABLE IF NOT EXISTS server_870478120_1 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

CREATE TABLE IF NOT EXISTS server_870478120_101 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

CREATE TABLE IF NOT EXISTS server_870478120_100 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

CREATE TABLE IF NOT EXISTS chats_gsync_870478120 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

CREATE TABLE IF NOT EXISTS chats_gsync_738733496 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

CREATE TABLE IF NOT EXISTS chats_gsync_488828183 (
    chat_id INTEGER NOT NULL,
    chat_title TEXT,
    PRIMARY KEY (chat_id)
);

-- Роли и права (пер-chat таблицы)
CREATE TABLE IF NOT EXISTS permissions_1 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_4 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_5 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_7 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_8 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_9 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_10 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_11 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_13 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_18 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_20 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_21 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_23 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_24 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_25 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_26 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_27 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_28 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_12 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_16 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_107 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_109 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_2 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permissions_3 (
    user_id BIGINT PRIMARY KEY,
    level BIGINT NOT NULL DEFAULT 0
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_businesses_user ON businesses(user_id);
CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory(user_id);
CREATE INDEX IF NOT EXISTS idx_cases_log_user ON cases_log(user_id, case_type, created_at);
CREATE INDEX IF NOT EXISTS idx_user_cases_user ON user_cases(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_auction_active ON auction_items(ends_at);
CREATE INDEX IF NOT EXISTS idx_exchange_logs_user ON exchange_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_admin_stats_user_date ON admin_stats(user_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_admin_norms_user_date ON admin_norms(user_id, norm_date);
CREATE INDEX IF NOT EXISTS idx_shop_orders_user ON shop_orders(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_shop_orders_payment ON shop_orders(payment_provider, payment_id);
CREATE INDEX IF NOT EXISTS idx_web_requests_status ON web_requests(status, created_at);
