"""
Миграция данных из SQLite (database.db) в PostgreSQL (Bothost).
Запуск: python migrate_to_postgres.py
"""
import sqlite3
import os
import sys

# Проверка psycopg2
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("Установите psycopg2: pip install psycopg2-binary")
    sys.exit(1)

# ===== НАСТРОЙКИ =====
SQLITE_PATH = "database.db"
PG_URL = os.environ.get(
    "BANANA_PG_URL",
    "postgresql://USER:PASSWORD@HOST:PORT/DATABASE"
)

# Таблицы для миграции (важные, с данными)
TABLES_TO_MIGRATE = [
    "businesses",
    "inventory",
    "cases_log",
    "user_cases",
    "auction_items",
    "user_effects",
    "permanent_prize_effects",
    "permanent_business_effects",
    "exchange_logs",
    "economy_wipe_state",
    "subscription_claims",
    "admin_epoints",
    "admin_daily_stats",
    "tech_action_forms",
    "admin_stats",
    "admin_norms",
    "reset_requests",
    "epoints_spends",
    "banana_pass_users",
    "banana_pass_claims",
    "banana_pass_daily_quests",
    "banana_pass_weekly_quests",
    "banana_pass_special_quests",
    "af_chat_settings",
    "af_config",
    "af_tech_config",
    "tech_chat_settings",
    "tech_permissions",
    "modified_chats",
    "custom_roles_4",
    "games_blacklist",
    "server_870478120_1",
    "server_870478120_101",
    "server_870478120_100",
    "chats_gsync_870478120",
    "chats_gsync_738733496",
    "chats_gsync_488828183",
]

# Таблицы per-chat (permissions_N, nicks_N, и т.д.) - нужно ли мигрировать?
PER_CHAT_PREFIXES = [
    "permissions_",
    "nicks_",
    "banwords_",
    "warns_",
    "mutes_",
    "mutelogs_",
    "bans_",
    "messages_",
    "warnhistory_",
    "punishments_",
]


def get_pg_connection():
    """Подключение к PostgreSQL"""
    from urllib.parse import urlparse
    parsed = urlparse(PG_URL)
    
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password
    )


def sqlite_to_pg_type(sqlite_type: str) -> str:
    """Конвертация типов SQLite → PostgreSQL"""
    mapping = {
        "INTEGER": "BIGINT",
        "BIGINT": "BIGINT",
        "TEXT": "TEXT",
        "TIMESTAMP": "TIMESTAMPTZ",
        "REAL": "DOUBLE PRECISION",
    }
    return mapping.get(sqlite_type.upper(), "TEXT")


def create_table_in_pg(conn, cursor, table_name, sqlite_conn):
    """Создать таблицу в PostgreSQL по схеме из SQLite"""
    cursor = sqlite_conn.cursor()
    cols = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    
    if not cols:
        print(f"  [SKIP] Нет колонок для {table_name}")
        return
    
    # Создаём PostgreSQL-специфичную схему
    pg_cols = []
    for col in cols:
        col_name = col[1]
        col_type = col[2]
        
        # Конвертируем тип
        pg_type = sqlite_to_pg_type(col_type)
        
        # Добавляем SERIAL для primary key
        if col[5] and col_name == "id":  # col[5] = pk flag
            pg_cols.append(f'{col_name} SERIAL PRIMARY KEY')
        else:
            pg_cols.append(f'{col_name} {pg_type}')
    
    # Добавляем created_at/updated_at если нет
    col_names = [c[1] for c in cols]
    if "created_at" not in col_names:
        pg_cols.append("created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP")
    if "updated_at" not in col_names:
        pg_cols.append("updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP")
    
    create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} (\n  " + ",\n  ".join(pg_cols) + "\n)"
    
    print(f"  Создаю таблицу: {table_name}")
    cursor.execute("DROP TABLE IF EXISTS " + table_name + " CASCADE")
    cursor.execute(create_sql)
    conn.commit()


def migrate_table(conn, cursor, table_name, sqlite_conn):
    """Мигрировать данные из одной таблицы"""
    cursor = sqlite_conn.cursor()
    
    # Получаем данные
    rows = cursor.execute(f"SELECT * FROM {table_name}").fetchall()
    if not rows:
        print(f"  [EMPTY] {table_name} - нет данных")
        return
    
    # Получаем имена колонок
    col_names = [d[0] for d in cursor.execute(f"SELECT * FROM {table_name} LIMIT 0").description]
    
    print(f"  Мигрирую {len(rows)} строк из {table_name}...")
    
    # Формируем placeholders для PostgreSQL
    placeholders = ",".join(["%s"] * len(col_names))
    cols_str = ",".join(col_names)
    
    # Вставляем данные
    for row in rows:
        try:
            cursor.execute(
                f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})",
                row
            )
        except Exception as e:
            # Пропускаем дубликаты
            pass
    
    conn.commit()
    print(f"    ✓ {len(rows)} строк")


def main():
    print("=" * 60)
    print("MIGRATION: SQLite → PostgreSQL (Bothost)")
    print("=" * 60)
    
    # Открываем SQLite
    print(f"\n[1/3] Открываю SQLite: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    
    # Открываем PostgreSQL
    print(f"[2/3] Подключаюсь к PostgreSQL...")
    pg_conn = get_pg_connection()
    pg_cursor = pg_conn.cursor()
    
    # Создаем расширения
    print("  Создаю расширения...")
    pg_cursor.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    pg_conn.commit()
    
    # Мигрируем таблицы
    print(f"\n[3/3] Миграция таблиц ({len(TABLES_TO_MIGRATE)} таблиц)...")
    print("-" * 60)
    
    for table in TABLES_TO_MIGRATE:
        # Проверяем есть ли таблица в SQLite
        tables_check = [r[0] for r in sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        
        if table not in tables_check:
            print(f"  [SKIP] {table} - не найдена в SQLite")
            continue
        
        # Создаём таблицу в PG
        create_table_in_pg(pg_conn, pg_cursor, table, sqlite_conn)
        
        # Мигрируем данные
        migrate_table(pg_conn, pg_cursor, table, sqlite_conn)
    
    print("-" * 60)
    print("\n✓ МИГРАЦИЯ ЗАВЕРШЕНА!")
    print(f"\nПодключение к PostgreSQL:")
    print(f"  {PG_URL[:80]}...")
    print("\nТеперь нужно обновить app.py для работы с PostgreSQL.")
    
    sqlite_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
