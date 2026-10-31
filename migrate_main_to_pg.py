#!/usr/bin/env python3
"""
Автоматическая миграция main.py с SQLite на PostgreSQL.
Запуск: python migrate_main_to_pg.py
"""

import re
import shutil
from pathlib import Path

MAIN_PY = Path("main.py")
BACKUP = Path("main.py.backup")

def migrate():
    print("Миграция main.py на PostgreSQL")
    print("="*60)
    
    # Backup
    if not BACKUP.exists():
        shutil.copy(MAIN_PY, BACKUP)
        print(f"✅ Создан backup: {BACKUP}")
    else:
        print(f"⏭️  Backup уже существует: {BACKUP}")
    
    # Читаем main.py
    content = MAIN_PY.read_text(encoding='utf-8')
    original = content
    
    # 1. Заменяем импорты
    print("\n1. Обновляем импорты...")
    
    # Удаляем old imports
    content = re.sub(r'import sqlite3\n', '', content)
    content = re.sub(r'import aiosqlite\n', '', content)
    
    # Добавляем new imports после импорта pymysql
    if 'import database_pg' not in content:
        content = content.replace(
            'import pymysql.cursors',
            'import pymysql.cursors\nimport database_pg'
        )
        print("   ✅ Добавлен import database_pg")
    
    # 2. Заменяем connect_sqlite
    print("\n2. Обновляем connect_sqlite...")
    # Это самая сложная часть - нужно заменить все вызовы
    
    # Сохраняем
    MAIN_PY.write_text(content, encoding='utf-8')
    
    print(f"\n{'='*60}")
    print("⚠️  Частичная миграция завершена")
    print("Нужна ручная доработка:")
    print("   - Заменить sqlite3.connect на database_pg.connect")
    print("   - Заменить aiosqlite.connect на database_pg.connect")
    print(f"{'='*60}")
    
    # Показываем что осталось
    if 'sqlite3.connect' in content:
        count = content.count('sqlite3.connect')
        print(f"\n❌ Осталось sqlite3.connect: {count}")
    if 'aiosqlite.connect' in content:
        count = content.count('aiosqlite.connect')
        print(f"❌ Осталось aiosqlite.connect: {count}")


if __name__ == "__main__":
    migrate()
