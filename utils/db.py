import database_pg_async as aiosqlite

DB_PATH = "database.db"


async def init_economy_schema():
    """
    Инициализирует все необходимые таблицы для экономической системы бота,
    включая бизнесы, инвентарь, эффекты пользователей и логи кейсов.
    """
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            '''
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            branch_no INTEGER NOT NULL DEFAULT 1,
            upgrade_level INTEGER NOT NULL DEFAULT 0,
            products INTEGER NOT NULL DEFAULT 100,
            branch_balance INTEGER NOT NULL DEFAULT 0,
            talisman_active INTEGER NOT NULL DEFAULT 0,
            regular_talisman_active INTEGER NOT NULL DEFAULT 0,
            talisman_bonus_percent INTEGER NOT NULL DEFAULT 0,
            last_collected_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            UNIQUE(user_id, business_key, branch_no)
        )'''
        )
        existing_columns = {
            row[0]
            for row in await (await db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'businesses'"
            )).fetchall()
        }
        if "last_collected_at" not in existing_columns:
            await db.execute("ALTER TABLE businesses ADD COLUMN last_collected_at TEXT")
        await db.execute(
            """
            UPDATE businesses
            SET products = 100
            WHERE products = 0
              AND branch_balance = 0
              AND (last_collected_at IS NULL OR last_collected_at::text = '')
            """
        )

        await db.execute(
            '''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_value INTEGER NOT NULL DEFAULT 0,
            acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )'''
        )

# Таблицы user_effects, permanent_prize_effects, permanent_business_effects удалены (этап 5 — предметы-бонусы отключены)


        await db.execute(
            '''
        CREATE TABLE IF NOT EXISTS cases_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            case_type TEXT NOT NULL,
            reward_type TEXT NOT NULL,
            reward_value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )'''
        )

        await db.execute(
            '''
        CREATE TABLE IF NOT EXISTS user_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            case_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )'''
        )

        await db.execute(
            '''
        CREATE TABLE IF NOT EXISTS auction_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_value INTEGER NOT NULL DEFAULT 0,
            start_bid INTEGER NOT NULL,
            current_bid INTEGER NOT NULL,
            highest_bidder_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ends_at TEXT NOT NULL,
            FOREIGN KEY (seller_id) REFERENCES users(user_id) ON DELETE CASCADE
        )'''
        )

        # Индексы для оптимизации запросов
        await db.execute("CREATE INDEX IF NOT EXISTS idx_businesses_user ON businesses(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cases_log_user ON cases_log(user_id, case_type, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_cases_user ON user_cases(user_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_auction_active ON auction_items(ends_at)")

        await db.commit()

    print("[DB] Схема экономической системы успешно инициализирована")
