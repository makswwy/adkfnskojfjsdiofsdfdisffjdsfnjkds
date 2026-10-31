# -*- coding: utf-8 -*-
"""
database_pg.py — PostgreSQL-адаптер, совместимый с API sqlite3.

Задача модуля — позволить существующему коду (написанному под SQLite)
работать с внешним PostgreSQL без изменений. Для этого адаптер:

  * транслирует SQLite-синтаксис в PostgreSQL
    (``?`` -> ``%s``, ``INSERT OR IGNORE/REPLACE``, ``PRAGMA``,
     ``sqlite_master``, ``datetime('now')``, ``rowid``, ``AUTOINCREMENT`` и т.д.);
  * держит пул соединений (сетевой коннект дорогой — ~2 сек);
  * возвращает строки в виде ``Row`` (совместим с ``sqlite3.Row``);
  * поддерживает ``lastrowid`` через ``RETURNING``.

Асинхронный API (совместимый с ``aiosqlite``) находится в
``database_pg_async.py``.
"""

import atexit
import re
import threading

import psycopg2
import psycopg2.extensions
import psycopg2.pool
from datetime import datetime, date
from decimal import Decimal

# ==================== КОНФИГ ====================

PG_CONFIG = {
    'host': 'node1.pghost.ru',
    'port': 16182,
    'dbname': 'bothost_db_4b8532d13210',
    'user': 'bothost_db_4b8532d13210',
    'password': 'Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ',
}

_POOL_MIN = 1
_POOL_MAX = 60

_pool = None
_pool_lock = threading.Lock()
_compat_done = False

# ==================== ПУЛ СОЕДИНЕНИЙ ====================


def _create_pool():
    return psycopg2.pool.ThreadedConnectionPool(
        _POOL_MIN,
        _POOL_MAX,
        host=PG_CONFIG['host'],
        port=PG_CONFIG['port'],
        dbname=PG_CONFIG['dbname'],
        user=PG_CONFIG['user'],
        password=PG_CONFIG['password'],
        connect_timeout=15,
        options='-c statement_timeout=60000 -c timezone=UTC',
    )


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _create_pool()
                _ensure_compat()
    return _pool


def _close_pool():
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None


atexit.register(_close_pool)


def _ensure_compat():
    """Создаёт объекты совместимости со SQLite (представление sqlite_master)."""
    global _compat_done
    if _compat_done:
        return
    conn = None
    try:
        conn = _pool.getconn()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE OR REPLACE VIEW sqlite_master AS
            SELECT 'table'::text AS type, tablename AS name,
                   tablename AS tbl_name, ''::text AS sql
            FROM pg_tables WHERE schemaname = 'public'
            UNION ALL
            SELECT 'index'::text AS type, indexname AS name,
                   tablename AS tbl_name, indexdef AS sql
            FROM pg_indexes WHERE schemaname = 'public'
            """
        )
        conn.commit()
        cur.close()
        _compat_done = True
    except Exception as error:  # noqa: BLE001
        print(f"[database_pg] Не удалось создать представление sqlite_master: {error}")
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                _pool.putconn(conn)
            except Exception:
                pass


# ==================== МЕТАДАННЫЕ ТАБЛИЦ ====================

_meta_lock = threading.Lock()
_meta_columns = {}   # table -> [column, ...]
_meta_pk = {}        # table -> [column, ...]
_meta_unique = {}    # table -> [[column, ...], ...] (PK + уникальные индексы)
_meta_serial = {}    # table -> column (sequence-колонка) или None


def _load_meta(conn, table):
    table = table.lower()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    columns = [r[0] for r in cur.fetchall()]
    if not columns:
        cur.close()
        return None

    cur.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
        WHERE i.indisprimary AND c.relname = %s
        ORDER BY a.attnum
        """,
        (table,),
    )
    pk = [r[0] for r in cur.fetchall()]

    # Все уникальные индексы (включая PK) — нужны для INSERT OR REPLACE,
    # т.к. в SQLite REPLACE срабатывает по любому UNIQUE-ограничению.
    cur.execute(
        """
        SELECT i.indexrelid::regclass::text AS idx,
               array_agg(a.attname ORDER BY k.ord) AS cols
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON k.attnum = a.attnum
        WHERE c.relname = %s AND i.indisunique AND i.indpred IS NULL
        GROUP BY i.indexrelid
        """,
        (table,),
    )
    unique = []
    for _idx, cols in cur.fetchall():
        unique.append(list(cols))

    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
          AND column_default LIKE 'nextval%%'
        LIMIT 1
        """,
        (table,),
    )
    serial_row = cur.fetchone()
    serial = serial_row[0] if serial_row else None
    cur.close()

    with _meta_lock:
        _meta_columns[table] = columns
        _meta_pk[table] = pk
        _meta_unique[table] = unique
        _meta_serial[table] = serial
    return table


def _get_meta(conn, table):
    table = (table or '').lower()
    if table in _meta_columns:
        return table
    try:
        return _load_meta(conn, table)
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ==================== ТРАНСЛЯЦИЯ SQL ====================

_RE_INSERT_OR = re.compile(
    r'INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE,
)
_RE_TABLE_OF_INSERT = re.compile(
    r'^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE,
)
_RE_INSERT_COLUMNS = re.compile(
    r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[A-Za-z_][A-Za-z0-9_]*\s*\(([^)]*)\)',
    re.IGNORECASE,
)
_RE_TIME_FUNC = re.compile(r"\b(datetime|date)\s*\(", re.IGNORECASE)
_RE_JULIANDAY = re.compile(r"\bjulianday\s*\(", re.IGNORECASE)
_RE_SCALAR_MAXMIN = re.compile(r"\b(MAX|MIN)\s*\(", re.IGNORECASE)
_CURRENT_TIMESTAMP_TOKEN = 'CURRENT_TIMESTAMP'
_CURRENT_TIMESTAMP_EXPR = "date_trunc('second', NOW() AT TIME ZONE 'UTC')"
_RE_PRAGMA_TABLE_INFO = re.compile(
    r"PRAGMA\s+table_info\s*\(\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\)",
    re.IGNORECASE,
)
_RE_PRAGMA_SIMPLE = re.compile(r"PRAGMA\s+([A-Za-z_]+)\s*(?:=\s*([^;]*))?;?", re.IGNORECASE)
_RE_INT_PK_AUTOINC = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE
)
_RE_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
_RE_ORDER_BY_ROWID = re.compile(r"(ORDER\s+BY\s+)rowid\b", re.IGNORECASE)
_RE_COLLATE_NOCASE = re.compile(r"\s+COLLATE\s+NOCASE\b", re.IGNORECASE)
_RE_BEGIN = re.compile(r"^\s*BEGIN(\s+(IMMEDIATE|EXCLUSIVE|DEFERRED))?\s*;?\s*$", re.IGNORECASE)
_RE_SQLITE_MASTER_LIKE = re.compile(r"name\s+NOT\s+LIKE\s+'sqlite_%'", re.IGNORECASE)


def _replace_qmarks(sql):
    """Заменяет ``?`` на ``%s`` вне строковых литералов."""
    out = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    in_bracket = False
    while i < n:
        ch = sql[i]
        if in_single:
            out.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            out.append(ch)
            if ch == '"':
                if i + 1 < n and sql[i + 1] == '"':
                    out.append('"')
                    i += 2
                    continue
                in_double = False
            i += 1
            continue
        if in_bracket:
            out.append(ch)
            if ch == ']':
                in_bracket = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == '[':
            in_bracket = True
            out.append(ch)
            i += 1
            continue
        if ch == '?':
            out.append('%s')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _replace_rowid(sql):
    """Заменяет одиночный токен ``rowid`` на ``ctid`` вне строковых литералов."""
    out = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    while i < n:
        ch = sql[i]
        if in_single:
            out.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            out.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if sql[i:i + 5].lower() == 'rowid':
            before = sql[i - 1] if i > 0 else ''
            after = sql[i + 5] if i + 5 < n else ''
            if not (before.isalnum() or before == '_') and not (after.isalnum() or after == '_'):
                # ``rowid`` как алиас (``ctid AS rowid``) не трогаем.
                prefix = sql[:i].rstrip()
                if not prefix.lower().endswith('as'):
                    out.append('ctid')
                    i += 5
                    continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _escape_percent(sql, has_params):
    """Удваивает ``%`` (кроме ``%s``), если запрос выполняется с параметрами."""
    if not has_params:
        return sql
    parts = sql.split('%s')
    parts = [p.replace('%', '%%') for p in parts]
    return '%s'.join(parts)


def _translate_insert_or(sql, conn, params):
    """Обрабатывает ``INSERT OR IGNORE`` и ``INSERT OR REPLACE``."""
    match = _RE_INSERT_OR.search(sql)
    if not match:
        return sql
    mode = match.group(1).upper()
    table = match.group(2)

    if mode == 'IGNORE':
        sql = _RE_INSERT_OR.sub(r'INSERT INTO \2', sql, count=1)
        if 'ON CONFLICT' not in sql.upper():
            sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        return sql

    # REPLACE
    sql = _RE_INSERT_OR.sub(r'INSERT INTO \2', sql, count=1)
    if 'ON CONFLICT' in sql.upper():
        return sql

    meta_table = _get_meta(conn, table)
    pk = _meta_pk.get((meta_table or '').lower()) if meta_table else None
    unique_sets = _meta_unique.get((meta_table or '').lower()) if meta_table else None

    cols_match = _RE_INSERT_COLUMNS.search(sql)
    columns = []
    if cols_match:
        columns = [c.strip().strip('"[]') for c in cols_match.group(1).split(',') if c.strip()]
    columns_lower = {c.lower() for c in columns}

    # Ищем уникальный индекс, все колонки которого присутствуют в INSERT
    # (SQLite REPLACE срабатывает по любому UNIQUE-ограничению, а не только по PK).
    conflict_cols = None
    if unique_sets:
        for uniq in unique_sets:
            if uniq and all(c.lower() in columns_lower for c in uniq):
                conflict_cols = uniq
                break
    if conflict_cols is None and pk and all(c.lower() in columns_lower for c in pk):
        conflict_cols = pk

    if not conflict_cols:
        # Без подходящего уникального ограничения безопасно деградируем до IGNORE.
        return sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'

    conflict_lower = {c.lower() for c in conflict_cols}
    updatable = [c for c in columns if c.lower() not in conflict_lower]
    if updatable:
        set_clause = ', '.join(f'{c} = EXCLUDED.{c}' for c in updatable)
        conflict_target = ', '.join(conflict_cols)
        sql = sql.rstrip().rstrip(';') + f' ON CONFLICT ({conflict_target}) DO UPDATE SET {set_clause}'
    else:
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


def _split_sql_args(text):
    """Делит список аргументов функции по запятым верхнего уровня."""
    args = []
    buf = []
    depth = 0
    in_single = False
    in_double = False
    for ch in text:
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            continue
        if ch == '(':
            depth += 1
            buf.append(ch)
            continue
        if ch == ')':
            depth -= 1
            buf.append(ch)
            continue
        if ch == ',' and depth == 0:
            args.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        args.append(tail)
    return args


def _find_matching_paren(sql, open_idx):
    """Индекс закрывающей скобки, парной к ``sql[open_idx] == '('``."""
    depth = 0
    in_single = False
    in_double = False
    i = open_idx
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_single:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _apply_sqlite_modifiers(base_expr, modifiers, with_time):
    """Применяет SQLite-модификаторы времени к timestamp-выражению (UTC)."""
    cur = base_expr
    for raw in modifiers:
        mod = raw.strip()
        if mod == '?':
            # Модификатор приходит как параметр (напр. '-7 days').
            cur = f"({cur} + (%s)::interval)"
            continue
        if mod.startswith("'") and mod.endswith("'") and len(mod) >= 2:
            mod = mod[1:-1]
        low = mod.lower()
        match = re.match(r'([+-]?\d+(?:\.\d+)?)\s*(second|minute|hour|day|month|year)s?', low)
        if match:
            amount = match.group(1)
            unit = match.group(2)
            cur = f"({cur} + INTERVAL '{amount} {unit}')"
        elif low.startswith('start of day'):
            cur = f"date_trunc('day', {cur})"
        elif low.startswith('start of month'):
            cur = f"date_trunc('month', {cur})"
        elif low.startswith('start of year'):
            cur = f"date_trunc('year', {cur})"
        elif low == 'now':
            cur = 'NOW() AT TIME ZONE \'UTC\''
        else:
            # Неизвестный модификатор — игнорируем (не ломаем запрос).
            continue
    if with_time:
        return f"date_trunc('second', {cur})"
    return f"({cur})::date"


def _coerce_coalesce_args(expr):
    """Оборачивает каждый аргумент верхнеуровневого COALESCE в ``::timestamptz``.

    Колонки дат в части таблиц мигрированы из SQLite как TEXT, а выражения
    вроде CURRENT_TIMESTAMP — timestamp; без приведения PG бросает
    DatatypeMismatch («COALESCE types text and timestamp cannot be matched»).
    Для настоящих timestamptz-колонок приведение — no-op.
    """
    stripped = expr.strip()
    match = re.match(r"(?i)^coalesce\s*\(", stripped)
    if not match:
        return expr
    open_idx = match.end() - 1
    close_idx = _find_matching_paren(stripped, open_idx)
    if close_idx == -1 or stripped[close_idx + 1:].strip():
        return expr
    args = _split_sql_args(stripped[open_idx + 1:close_idx])
    if not args:
        return expr
    coerced = ", ".join(f"(({arg})::timestamptz)" for arg in args)
    return f"COALESCE({coerced})"


def _translate_time_functions(sql, conn=None):
    """Транслирует ``datetime(...)`` и ``date(...)`` с учётом модификаторов.

    Возвращает timestamp без таймзоны в UTC, обрезанный до секунд — так же,
    как это делает SQLite. Это одинаково корректно и в сравнениях
    (``datetime(col) <= datetime('now')``), и при выводе строки.
    """
    result = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            result.append(sql[i:j + 1])
            i = j + 1
            continue

        match = _RE_TIME_FUNC.match(sql, i)
        if match:
            func = match.group(1).lower()
            open_idx = match.end() - 1
            close_idx = _find_matching_paren(sql, open_idx)
            if close_idx == -1:
                result.append(ch)
                i += 1
                continue
            inner = sql[open_idx + 1:close_idx]
            args = _split_sql_args(inner)
            if not args:
                result.append(sql[i:close_idx + 1])
                i = close_idx + 1
                continue
            first = args[0].strip()
            if first.lower() == "'now'":
                base = "(NOW() AT TIME ZONE 'UTC')"
            else:
                first = _coerce_coalesce_args(first)
                base = f"(({first})::timestamptz AT TIME ZONE 'UTC')"
            with_time = func == 'datetime'
            expr = _apply_sqlite_modifiers(base, args[1:], with_time)
            if not with_time:
                expr = f"({expr})::text"
            result.append(expr)
            i = close_idx + 1
            continue

        result.append(ch)
        i += 1
    return ''.join(result)



def _skip_string(sql, i):
    """Возвращает индекс сразу после строкового литерала, начинающегося с ``i``."""
    n = len(sql)
    j = i + 1
    while j < n:
        if sql[j] == "'":
            if j + 1 < n and sql[j + 1] == "'":
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _translate_julianday(sql):
    """Транслирует ``julianday(x)`` в юлианский день (как в SQLite).

    SQLite возвращает количество дней (float). Код умножает разницу
    ``julianday(a) - julianday('now')`` на 86400, ожидая секунды.
    """
    result = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            end = _skip_string(sql, i)
            result.append(sql[i:end])
            i = end
            continue

        match = _RE_JULIANDAY.match(sql, i)
        if match:
            open_idx = match.end() - 1
            close_idx = _find_matching_paren(sql, open_idx)
            if close_idx == -1:
                result.append(ch)
                i += 1
                continue
            inner = sql[open_idx + 1:close_idx].strip()
            if inner.lower() in ("'now'", 'now'):
                base = "(NOW() AT TIME ZONE 'UTC')"
            else:
                base = f"(({inner})::timestamptz AT TIME ZONE 'UTC')"
            expr = (
                "(2440587.5 + EXTRACT(EPOCH FROM "
                f"(({base}) - TIMESTAMP '1970-01-01 00:00:00')) / 86400.0)"
            )
            result.append(expr)
            i = close_idx + 1
            continue

        result.append(ch)
        i += 1
    return ''.join(result)


def _translate_scalar_maxmin(sql):
    """``MAX(a, b)``/``MIN(a, b)`` (SQLite) -> ``GREATEST``/``LEAST`` (PostgreSQL).

    Агрегатные вызовы с одним аргументом (``MAX(col)``) не трогаем.
    """
    result = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            end = _skip_string(sql, i)
            result.append(sql[i:end])
            i = end
            continue

        match = _RE_SCALAR_MAXMIN.match(sql, i)
        if match:
            open_idx = match.end() - 1
            close_idx = _find_matching_paren(sql, open_idx)
            if close_idx == -1:
                result.append(ch)
                i += 1
                continue
            inner = sql[open_idx + 1:close_idx]
            args = _split_sql_args(inner)
            if len(args) >= 2:
                new_args = [_translate_scalar_maxmin(a) for a in args]
                func = 'GREATEST' if match.group(1).upper() == 'MAX' else 'LEAST'
                result.append(f"{func}({', '.join(new_args)})")
                i = close_idx + 1
                continue
            result.append(sql[i:close_idx + 1])
            i = close_idx + 1
            continue

        result.append(ch)
        i += 1
    return ''.join(result)


def _replace_current_timestamp(sql):
    """Заменяет ``CURRENT_TIMESTAMP`` на UTC-время с точностью до секунды.

    PG по умолчанию отдаёт микросекунды и смещение, SQLite — ``YYYY-MM-DD HH:MM:SS``.
    """
    token = _CURRENT_TIMESTAMP_TOKEN
    result = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            end = _skip_string(sql, i)
            result.append(sql[i:end])
            i = end
            continue
        if sql[i:i + len(token)].upper() == token:
            before = sql[i - 1] if i > 0 else ''
            after = sql[i + len(token)] if i + len(token) < n else ''
            if not (before.isalnum() or before == '_') and not (after.isalnum() or after == '_'):
                result.append(_CURRENT_TIMESTAMP_EXPR)
                i += len(token)
                continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _translate_pragma(sql, conn, params):
    """Транслирует PRAGMA-запросы."""
    info = _RE_PRAGMA_TABLE_INFO.search(sql)
    if info:
        table = info.group(1).lower()
        return (
            "SELECT (ordinal_position - 1)::int AS cid, column_name AS name, "
            "data_type AS type, "
            "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
            "column_default AS dflt_value, 0 AS pk "
            "FROM information_schema.columns "
            f"WHERE table_name = '{table}' ORDER BY ordinal_position"
        )

    simple = _RE_PRAGMA_SIMPLE.search(sql)
    if simple:
        name = simple.group(1).lower()
        if name == 'integrity_check':
            return "SELECT 'ok' AS integrity_check"
        if name == 'page_count':
            return "SELECT 0 AS page_count"
        if name == 'page_size':
            return "SELECT 4096 AS page_size"
        # journal_mode / synchronous / busy_timeout / foreign_keys -> no-op
        return "SELECT 1 WHERE false"
    return sql


def translate(sql, conn=None, params=None):
    """Переводит SQLite-запрос в PostgreSQL-совместимый вид."""
    if sql is None:
        return sql
    if isinstance(sql, bytes):
        sql = sql.decode('utf-8', 'replace')

    original = sql.strip()
    upper = original.upper()

    if _RE_BEGIN.match(original):
        return None  # psycopg2 сам управляет транзакциями

    # PRAGMA
    if upper.startswith('PRAGMA'):
        sql = _translate_pragma(sql, conn, params)
        if sql is None:
            return None
        return _escape_percent(sql, bool(params))

    # INSERT OR IGNORE / REPLACE
    if 'INSERT OR ' in upper:
        sql = _translate_insert_or(sql, conn, params)

    # datetime('now') / datetime(col) / datetime(col, '+N days') и date(...)
    sql = _translate_time_functions(sql, conn)

    # julianday(x) -> юлианский день (float, как в SQLite)
    sql = _translate_julianday(sql)

    # MAX(a, b) / MIN(a, b) -> GREATEST / LEAST
    sql = _translate_scalar_maxmin(sql)

    # CURRENT_TIMESTAMP -> UTC-время до секунд (совместимо с SQLite)
    sql = _replace_current_timestamp(sql)

    # rowid -> ctid
    sql = _RE_ORDER_BY_ROWID.sub(r'\1ctid', sql)
    sql = _replace_rowid(sql)

    # CREATE TABLE: AUTOINCREMENT / INTEGER PRIMARY KEY
    if upper.startswith('CREATE TABLE') or 'AUTOINCREMENT' in upper:
        sql = _RE_INT_PK_AUTOINC.sub('BIGSERIAL PRIMARY KEY', sql)
        sql = _RE_AUTOINCREMENT.sub('', sql)

    # COLLATE NOCASE -> без COLLATE (в PG нет такой коллации в DDL)
    sql = _RE_COLLATE_NOCASE.sub('', sql)

    # placeholders и проценты
    sql = _replace_qmarks(sql)
    sql = _escape_percent(sql, bool(params))
    return sql


def _needs_returning_id(sql, conn):
    """Определяет, нужно ли добавить ``RETURNING <serial>`` для lastrowid."""
    match = _RE_TABLE_OF_INSERT.match(sql)
    if not match:
        return None
    if 'RETURNING' in sql.upper():
        return None
    table = match.group(1)
    meta_table = _get_meta(conn, table)
    if not meta_table:
        return None
    serial = _meta_serial.get(meta_table.lower())
    return serial or None


# ==================== ЗНАЧЕНИЯ И СТРОКИ ====================


def _process_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=' ')
    if isinstance(value, Decimal):
        return float(value)
    return value


class Row:
    """Совместим с ``sqlite3.Row``."""

    __slots__ = ('_data',)

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data.values())

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f"Row({self._data!r})"


def _build_row(cursor, values):
    if values is None:
        return None
    if cursor.description is None:
        return values
    cols = [d[0] for d in cursor.description]
    return Row({c: _process_value(v) for c, v in zip(cols, values)})


# ==================== SYNC API (совместим с sqlite3) ====================


class Cursor:
    def __init__(self, conn, pg_cursor):
        self._conn = conn
        self._cursor = pg_cursor
        self.rowcount = -1
        self.lastrowid = None
        self._lastrowid_value = None

    def execute(self, sql, params=None):
        params = tuple(params) if params else None
        if self._conn is not None:
            self._conn._ensure_meta_ready()
            # Test if connection is alive, reconnect if dead
            if self._cursor is None or self._cursor.closed:
                try:
                    self._conn._pg.close()
                except Exception:
                    pass
                try:
                    self._conn._pool.putconn(self._conn._pg, close=True)
                except Exception:
                    pass
                self._conn._pg = self._conn._pool.getconn()
                self._conn._pg.autocommit = self._conn._autocommit
                self._cursor = self._conn._pg.cursor()
            else:
                try:
                    self._cursor.execute("SELECT 1")
                except Exception:
                    # Connection is dead, reconnect
                    try:
                        self._conn._pg.close()
                    except Exception:
                        pass
                    try:
                        self._conn._pool.putconn(self._conn._pg, close=True)
                    except Exception:
                        pass
                    self._conn._pg = self._conn._pool.getconn()
                    self._conn._pg.autocommit = self._conn._autocommit
                    self._cursor = self._conn._pg.cursor()
        # Reopen cursor if closed
        if self._cursor is None or self._cursor.closed:
            if self._conn is not None and self._conn._pg is not None:
                self._cursor = self._conn._pg.cursor()
        translated = translate(sql, self._conn._pg if self._conn else None, params)

        if translated is None:
            self.rowcount = 0
            return self

        serial = _needs_returning_id(translated, self._conn._pg) if self._conn else None
        if serial:
            translated = translated.rstrip().rstrip(';') + f' RETURNING {serial}'

        if params:
            try:
                self._cursor.execute(translated, params)
            except psycopg2.errors.DuplicateColumn as error:
                # SQLite при повторном ALTER TABLE ADD COLUMN бросает
                # sqlite3.OperationalError("duplicate column name: ...") —
                # воспроизводим ту же семантику для миграций.
                raise OperationalError(f"duplicate column name: {error}") from error
        else:
            try:
                self._cursor.execute(translated)
            except psycopg2.errors.DuplicateColumn as error:
                raise OperationalError(f"duplicate column name: {error}") from error

        self.rowcount = self._cursor.rowcount
        if serial:
            try:
                row = self._cursor.fetchone()
                if row is not None:
                    self._lastrowid_value = row[0]
            except Exception:  # noqa: BLE001
                self._lastrowid_value = None
            self.lastrowid = self._lastrowid_value
        elif self._cursor.rowcount == 1 and translated.upper().lstrip().startswith('INSERT'):
            # Note: LASTVAL() is intentionally NOT called here because it can
            # abort the transaction if no sequence was used. Use RETURNING instead.
            self.lastrowid = None
        return self

    def executemany(self, sql, params_list):
        params_list = [tuple(p) for p in params_list]
        translated = translate(sql, self._conn._pg if self._conn else None, params_list[0] if params_list else None)
        if translated is None:
            self.rowcount = 0
            return self
        self._cursor.executemany(translated, params_list)
        self.rowcount = self._cursor.rowcount
        return self

    def executescript(self, script):
        for statement in _split_statements(script):
            self.execute(statement)
        return self

    def fetchone(self):
        return _build_row(self._cursor, self._cursor.fetchone())

    def fetchall(self):
        return [_build_row(self._cursor, r) for r in self._cursor.fetchall()]

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany(size) if size else self._cursor.fetchmany()
        return [_build_row(self._cursor, r) for r in rows]

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class Connection:
    """Совместим с ``sqlite3.Connection``."""

    def __init__(self, db_path="database.db", timeout=30, check_same_thread=True,
                 isolation_level=None, **_kwargs):
        self.row_factory = None
        self._pool = _get_pool()
        self._pg = self._pool.getconn()
        # В sqlite3 isolation_level=None означает автокоммит. Воспроизводим это,
        # иначе долгоживущий глобальный коннект держал бы открытую транзакцию
        # и блокировал бы записи из других соединений.
        self._autocommit = isolation_level is None
        self._pg.autocommit = self._autocommit
        self._closed = False
        self._meta_ready = False

    def _ensure_meta_ready(self):
        if not self._meta_ready:
            self._meta_ready = True

    def cursor(self):
        return Cursor(self, self._pg.cursor())

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, params_list):
        return self.cursor().executemany(sql, params_list)

    def executescript(self, sql_script):
        cur = self.cursor()
        cur.executescript(sql_script)
        return cur

    def commit(self):
        if not self._closed:
            self._pg.commit()

    def rollback(self):
        if not self._closed:
            self._pg.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._pg.rollback()
        except Exception:
            pass
        try:
            self._pool.putconn(self._pg)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                self.rollback()
            except Exception:
                pass
        else:
            try:
                self.commit()
            except Exception:
                pass
        self.close()
        return False


def _split_statements(script):
    statements = []
    buf = []
    in_single = False
    for ch in script:
        if ch == "'":
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == ';' and not in_single:
            stmt = ''.join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            continue
        buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


# ==================== ЭКСПОРТ (sqlite3 API) ====================

connect = Connection
OperationalError = psycopg2.OperationalError
IntegrityError = psycopg2.IntegrityError
ProgrammingError = psycopg2.ProgrammingError
DatabaseError = psycopg2.DatabaseError
InterfaceError = psycopg2.InterfaceError
DataError = psycopg2.DataError
InternalError = psycopg2.InternalError
NotSupportedError = psycopg2.NotSupportedError
Warning = psycopg2.Warning
Error = psycopg2.Error