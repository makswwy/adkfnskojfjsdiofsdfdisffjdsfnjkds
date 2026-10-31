# Скрипт миграции SQLite -> PostgreSQL

## Что делает
Переносит ВСЕ данные из локальной SQLite базы (`database.db`) в PostgreSQL на Bothost БЕЗ ПОТЕРЬ.

## Как использовать

### 1. Загрузить файлы на хостинг
- `migrate_sqlite_to_postgres.py` — основной скрипт миграции
- `requirements.txt` — обновлённый (добавлен psycopg2-binary)

### 2. Установить зависимости
```bash
pip install psycopg2-binary
```

### 3. Запустить миграцию
```bash
python migrate_sqlite_to_postgres.py
```

### 4. Проверить результат
Скрипт покажет:
- Сколько таблиц перенесено
- Сколько строк в каждой таблице
- Ошибки (если были)

## Что переносится
- ✅ Экономика (inventory, user_effects, permanent_effects)
- ✅ Banana Pass (users, claims, quests)
- ✅ Администрирование (epoints, stats, norms, forms)
- ✅ Страны и мир (countries, country_*, autonomous_*, world_*)
- ✅ Игры (auction, cases, games_blacklist)
- ✅ Обмен (exchange_logs)
- ✅ Репутация (reputation, reputation_votes)
- ✅ Конфиги (af_*, tech_*)
- ✅ Сообщество (community_*)

## Что НЕ переносится (динамические таблицы)
Эти таблицы создаются автоматически при работе бота для каждого чата:
- `permissions_NNN` — права пользователей
- `nicks_NNN` — ники
- `bans_NNN` — баны
- `mutes_NNN` — муты
- `warns_NNN` — варны
- `messages_NNN` — сообщения
- И другие `*_NNN` таблицы

## После миграции
1. Проверить данные в PostgreSQL
2. Обновить `main.py` для подключения к PostgreSQL (вместо SQLite)
3. Удалить `database.db` (опционально)

## Важные заметки
- Большие числа (формат `big:123456789`) конвертируются автоматически
- Все типы данных SQLite конвертируются в PostgreSQL
- Миграция безопасна — SQLite база остаётся нетронутой
- Если ошибка — можно перезапустить скрипт (дубликаты обрабатываются)
