CREATE TABLE IF NOT EXISTS cases_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    case_type TEXT NOT NULL,
    reward_type TEXT NOT NULL,
    reward_value TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_name TEXT NOT NULL,
    item_value INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    business_key TEXT NOT NULL,
    branch_no INTEGER NOT NULL DEFAULT 1,
    upgrade_level INTEGER NOT NULL DEFAULT 0,
    products INTEGER NOT NULL DEFAULT 100,
    branch_balance INTEGER NOT NULL DEFAULT 0,
    talisman_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bananas (
    user_id INTEGER PRIMARY KEY,
    amount INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stat_date DATE NOT NULL,
    messages_count INTEGER DEFAULT 0,
    gbanpl_count INTEGER DEFAULT 0,
    gban_count INTEGER DEFAULT 0,
    epoints_earned INTEGER DEFAULT 0,
    norm_status TEXT DEFAULT 'not_completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, stat_date)
);

CREATE TABLE IF NOT EXISTS admin_norms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    norm_date DATE NOT NULL,
    forms_submitted INTEGER DEFAULT 0,
    forms_accepted INTEGER DEFAULT 0,
    resets_done INTEGER DEFAULT 0,
    norm_status TEXT DEFAULT 'not_completed',
    epoints_reward INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, norm_date)
);

CREATE INDEX IF NOT EXISTS idx_cases_log_user_id ON cases_log(user_id);
CREATE INDEX IF NOT EXISTS idx_inventory_user_id ON inventory(user_id);
CREATE INDEX IF NOT EXISTS idx_businesses_user_id ON businesses(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_stats_user_date ON admin_stats(user_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_admin_norms_user_date ON admin_norms(user_id, norm_date);
