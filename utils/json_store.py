# -*- coding: utf-8 -*-
"""
json_store.py — хранилище JSON-данных во внешней PostgreSQL.

Раньше экономика бота жила в .json-файлах рядом с main.py. Теперь все
такие данные хранятся в таблице json_store внешней базы (ключ = имя
файла, значение = JSONB), чтобы бот не зависел от локальных файлов.

Совместимость:
  * load(path) — читает значение из БД; если ключа ещё нет, но рядом лежит
    старый .json-файл, файл один раз импортируется в БД (сам файл не
    удаляется — остаётся резервной копией);
  * save(path, data) — UPSERT значения (последняя запись побеждает);
  * delete(path) — удаляет ключ (аналог os.remove для .json).

Каждый вызов берёт соединение из пула database_pg и возвращает его —
соединениями нельзя делиться между потоками (psycopg2.threadsafety = 2).
"""

import json
import os
import threading
import time
from datetime import datetime, timezone

import database_pg as sqlite3

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS json_store (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_ensure_lock = threading.Lock()
_table_ready = False


def _connect():
    # Путь к файлу адаптером игнорируется — всегда внешняя PostgreSQL.
    return sqlite3.connect("database.db")


def ensure_json_store() -> None:
    """Создаёт таблицу json_store (один раз на процесс)."""
    global _table_ready
    if _table_ready:
        return
    with _ensure_lock:
        if _table_ready:
            return
        last_error = None
        for _attempt in range(3):
            conn = _connect()
            try:
                conn.execute(TABLE_SQL)
                conn.commit()
                _table_ready = True
                return
            except Exception as error:  # noqa: BLE001
                last_error = error
                time.sleep(0.3)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        raise last_error


def _key(path) -> str:
    return os.path.basename(str(path))


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _read_row(key: str):
    """Возвращает JSON-строку значения или None (ошибка чтения → None + лог)."""
    conn = _connect()
    try:
        cur = conn.execute("SELECT data FROM json_store WHERE key = ?", (key,))
        row = cur.fetchone()
        return None if row is None else row[0]
    except Exception as error:  # noqa: BLE001
        print(f"[json_store] Ошибка чтения '{key}': {error}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _write(key: str, data, if_missing: bool = False) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    if if_missing:
        conflict = " ON CONFLICT (key) DO NOTHING"
    else:
        conflict = (
            " ON CONFLICT (key) DO UPDATE SET "
            "data = EXCLUDED.data, updated_at = EXCLUDED.updated_at"
        )
    query = "INSERT INTO json_store (key, data, updated_at) VALUES (?, ?::jsonb, ?)" + conflict
    conn = _connect()
    try:
        conn.execute(query, (key, payload, _now_utc()))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _import_file(path: str, key: str, file_pairs_hook) -> object:
    """Однократный импорт локального .json-файла в json_store.

    Возвращает распарсенные данные либо None, если файла нет или он битый.
    Уже существующий ключ в БД не перезаписывает.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            if file_pairs_hook is not None:
                data = json.load(file, object_pairs_hook=file_pairs_hook)
            else:
                data = json.load(file)
    except Exception as error:  # noqa: BLE001
        print(f"[json_store] Не удалось импортировать файл '{path}': {error}")
        return None
    try:
        _write(key, data, if_missing=True)
        print(f"[json_store] Файл '{path}' импортирован во внешнюю БД (ключ '{key}').")
    except Exception as error:  # noqa: BLE001
        # Данные всё равно возвращаем — работать можно и без записи ключа.
        print(f"[json_store] Не удалось сохранить импорт '{key}': {error}")
    return data


def load(path, default=None, file_pairs_hook=None):
    """Читает значение по ключу (имени файла) из внешней БД.

    Если ключа ещё нет — один раз импортирует локальный .json-файл, если он
    есть. Нет ни ключа, ни файла → возвращается default (ничего не пишется).
    """
    key = _key(path)
    ensure_json_store()
    raw = _read_row(key)
    if raw is not None:
        try:
            if isinstance(raw, (str, bytes)):
                return json.loads(raw)
            return raw
        except Exception as error:  # noqa: BLE001
            print(f"[json_store] Повреждённое значение '{key}': {error}")
            return default
    imported = _import_file(str(path), key, file_pairs_hook)
    if imported is not None:
        return imported
    return default


def save(path, data) -> None:
    """UPSERT значения во внешней БД (последняя запись побеждает)."""
    key = _key(path)
    ensure_json_store()
    _write(key, data)


def delete(path) -> None:
    """Удаляет ключ (аналог os.remove для .json), ошибки не бросает."""
    key = _key(path)
    try:
        ensure_json_store()
        conn = _connect()
        try:
            conn.execute("DELETE FROM json_store WHERE key = ?", (key,))
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as error:  # noqa: BLE001
        print(f"[json_store] Не удалось удалить '{key}': {error}")
