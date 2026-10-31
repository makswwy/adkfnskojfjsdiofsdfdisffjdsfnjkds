#!/usr/bin/env python3
"""
Скрипт для переноса данных из SQLite (database.db) в PostgreSQL (Bothost).
Запуск: python migrate_sqlite_to_postgres.py

ИСПРАВЛЕННАЯ ВЕРСИЯ:
- Фикс ошибки с execute_values
- Уменьшен размер батча для больших таблиц
- Добавлен progress bar
"""

import sqlite3
import psycopg2
import sys
import time

# ==================== НАСТРОЙКИ ====================
SQLITE_DB = "database.db"

PG_HOST = "node1.pghost.ru"
PG_PORT = 16182
PG_DB = "bothost_db_4b8532d13210"
PG_USER = "bothost_db_4b8532d13210"
PG_PASSWORD = "Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ"

# Таблицы для пропуска (динамические)
SKIP_PREFIXES = []


def is_dynamic_table(table_name):
    for prefix in SKIP_PREFIXES:
        if table_name.startswith(prefix):
            return True
    return False


def sqlite_type_to_pg(sqlite_type):
    type_map = {
        "INTEGER": "BIGINT",
        "BIGINT": "BIGINT",
        "TEXT": "TEXT",
        "TIMESTAMP": "TIMESTAMPTZ",
        "REAL": "DOUBLE PRECISION",
        "BLOB": "BYTEA",
        "NUMERIC": "NUMERIC",
    }
    if sqlite_type:
        upper = sqlite_type.upper()
        for key, val in type_map.items():
            if key in upper:
                return val
    return "TEXT"


def migrate_table(pg_conn, pg_cur, sqlite_conn, table_name, batch_size=500):
    """Переносит одну таблицу"""
    print(f"\n{'='*60}")
    print(f"Миграция: {table_name}")
    print(f"{'='*60}")
    
    try:
        cursor = sqlite_conn.cursor()
        
        # Получаем CREATE TABLE
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        row = cursor.fetchone()
        create_sql = row[0] if row else None
        
        # Получаем колонки
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = cursor.fetchall()
        
        if not columns:
            print(f"  ⚠️ Нет колонок, пропускаем")
            return True
        
        # Получаем количество строк
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        total_count = cursor.fetchone()[0]
        print(f"  Строк: {total_count}")
        
        if total_count == 0:
            print(f"  ✅ Пустая таблица")
            return True
        
        col_names = [col[1] for col in columns]
        
        # 1. Удаляем таблицу если существует (CASCADE) - чтобы пересоздать с правильными типами
        try:
            pg_cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
            pg_conn.commit()
        except Exception as e:
            print(f"  ⚠️ Ошибка DROP: {e}")
            pg_conn.rollback()
        
        # Создаём таблицу в PostgreSQL
        try:
            if create_sql:
                pg_create = create_sql
                # Заменяем INTEGER PRIMARY KEY → BIGSERIAL
                pg_create = pg_create.replace("INTEGER PRIMARY KEY", "BIGSERIAL")
                pg_create = pg_create.replace("AUTOINCREMENT", "")
                # Заменяем ВСЕ INTEGER → BIGINT
                pg_create = pg_create.replace("INTEGER", "BIGINT")
                # Убираем COLLATE NOCASE
                pg_create = pg_create.replace("COLLATE NOCASE", "")
                # Убираем COLLATE BINARY
                pg_create = pg_create.replace("COLLATE BINARY", "")
                pg_cur.execute(pg_create)
            else:
                col_defs = []
                for col in columns:
                    col_name = col[1]
                    col_type = sqlite_type_to_pg(col[2])
                    col_defs.append(f'"{col_name}" {col_type}')
                pg_create = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(col_defs)})'
                pg_cur.execute(pg_create)
            pg_conn.commit()
            print(f"  ✅ Таблица готова")
        
        except Exception as e:
            print(f"  ⚠️ Ошибка создания: {e}")
            pg_conn.rollback()
            col_defs = []
            for col in columns:
                col_name = col[1]
                col_type = sqlite_type_to_pg(col[2])
                col_defs.append(f'"{col_name}" {col_type}')
            pg_create = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(col_defs)})'
            pg_cur.execute(pg_create)
            pg_conn.commit()
            print(f"  ✅ Таблица создана (альтернативный)")
        
        cursor.execute(f'SELECT * FROM "{table_name}"')
        
        # Вставляем в PostgreSQL построчно (без execute_values)
        inserted = 0
        error_rows = 0
        
        # Формируем INSERT без VALUES %s
        placeholders = ", ".join(["%s"] * len(col_names))
        columns_str = ", ".join([f'"{col}"' for col in col_names])
        insert_sql = f'INSERT INTO "{table_name}" ({columns_str}) VALUES ({placeholders})'
        
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            
            batch = []
            for row in rows:
                cleaned = []
                for i, val in enumerate(row):
                    if val is None:
                        cleaned.append(None)
                        continue
                    
                    s = str(val)
                    # Обработка big:
                    if s.startswith("big:"):
                        try:
                            cleaned.append(int(s[4:]))
                            continue
                        except:
                            pass
                    
                    # Конвертация типов
                    col_type = str(columns[i][2] or "").upper()
                    if col_type in ("INTEGER", "BIGINT"):
                        try:
                            cleaned.append(int(val))
                        except (ValueError, TypeError):
                            cleaned.append(val)
                    elif col_type in ("DOUBLE PRECISION", "REAL"):
                        try:
                            cleaned.append(float(val))
                        except (ValueError, TypeError):
                            cleaned.append(val)
                    else:
                        cleaned.append(val)
                
                batch.append(tuple(cleaned))
            
            if batch:
                try:
                    pg_cur.executemany(insert_sql, batch)
                    inserted += len(batch)
                    
                    # Показываем прогресс каждые 10000 строк
                    if inserted % 10000 < len(batch):
                        pct = (inserted / total_count) * 100
                        print(f"  Прогресс: {inserted}/{total_count} ({pct:.1f}%)")
                    
                except Exception as e:
                    pg_conn.rollback()
                    error_rows += 1
                    if error_rows <= 3:
                        print(f"  ⚠️ Ошибка вставки: {e}")
            
            # Фиксируем каждую итерацию
            pg_conn.commit()
        
        print(f"  ✅ Вставлено: {inserted} строк")
        if error_rows > 0:
            print(f"  ⚠️ Ошибок: {error_rows}")
        
        return True
    
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("="*60)
    print("Миграция SQLite -> PostgreSQL (ИСПРАВЛЕННАЯ)")
    print("="*60)
    
    start_time = time.time()
    
    # SQLite
    print(f"\n📂 Открываем SQLite: {SQLITE_DB}")
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [row[0] for row in cursor.fetchall()]
    print(f"📋 Всего таблиц: {len(all_tables)}")
    
    tables_to_migrate = [t for t in all_tables if not is_dynamic_table(t)]
    print(f"📦 Будет перенесено: {len(tables_to_migrate)} таблиц")
    
    # PostgreSQL
    print(f"\n🌐 Подключаемся к PostgreSQL...")
    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            connect_timeout=30
        )
        pg_cur = pg_conn.cursor()
        print("✅ Подключено")
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        sys.exit(1)
    
    # Миграция
    success = 0
    errors = 0
    
    for i, table_name in enumerate(tables_to_migrate, 1):
        print(f"\n[{i}/{len(tables_to_migrate)}]")
        
        # Определяем размер батча
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cursor.fetchone()[0]
        batch_size = 100 if count > 100000 else 500
        
        try:
            if migrate_table(pg_conn, pg_cur, sqlite_conn, table_name, batch_size=batch_size):
                success += 1
            else:
                errors += 1
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}")
            errors += 1
    
    sqlite_conn.close()
    pg_conn.close()
    
    elapsed = time.time() - start_time
    
    print(f"\n{'='*60}")
    print(f"МИГРАЦИЯ ЗАВЕРШЕНА")
    print(f"{'='*60}")
    print(f"✅ Успешно: {success}")
    print(f"❌ Ошибок: {errors}")
    print(f"⏱️  Время: {elapsed:.1f} сек")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
