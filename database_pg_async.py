# -*- coding: utf-8 -*-
"""
database_pg_async.py — асинхронный PostgreSQL-адаптер, совместимый с aiosqlite.

Предоставляет ``connect()`` (и алиас ``async_connect``), возвращающий
``AsyncConnection``. Использует общий пул соединений из ``database_pg`` и
выполняет блокирующие вызовы psycopg2 в отдельном потоке (``asyncio.to_thread``),
чтобы не блокировать event loop.

Трансляция SQLite-синтаксиса выполняется тем же кодом, что и в ``database_pg``.
"""

import asyncio

import psycopg2
import psycopg2.extensions

import database_pg as _sync

# ==================== ПУЛ ====================


def _get_pool():
    return _sync._get_pool()


# ==================== КУРСОР ====================


class AsyncCursor:
    """Совместим с ``aiosqlite.Cursor``."""

    def __init__(self, conn, pg_cursor):
        self._conn = conn
        self._cursor = pg_cursor
        self.rowcount = -1
        self.lastrowid = None
        self._description = None

    async def execute(self, sql, params=None):
        await self._conn._ensure_ready()
        # Reopen cursor if closed
        if self._cursor is None or (hasattr(self._cursor, 'closed') and self._cursor.closed):
            self._cursor = self._conn._pg.cursor()
        params = tuple(params) if params else None
        translated = _sync.translate(sql, self._conn._pg, params)

        if translated is None:
            self.rowcount = 0
            return self

        serial = _sync._needs_returning_id(translated, self._conn._pg)
        if serial:
            translated = translated.rstrip().rstrip(';') + f' RETURNING {serial}'

        def _run():
            if params:
                self._cursor.execute(translated, params)
            else:
                self._cursor.execute(translated)
            self.rowcount = self._cursor.rowcount
            if serial:
                row = self._cursor.fetchone()
                self.lastrowid = row[0] if row is not None else None
            # Note: LASTVAL() is intentionally NOT called here because it can
            # abort the transaction if no sequence was used. Use RETURNING instead.
            self.lastrowid = None

        await asyncio.to_thread(_run)
        return self

    async def executemany(self, sql, params_list):
        await self._conn._ensure_ready()
        params_list = [tuple(p) for p in params_list]
        translated = _sync.translate(sql, self._conn._pg, params_list[0] if params_list else None)
        if translated is None:
            self.rowcount = 0
            return self

        def _run():
            self._cursor.executemany(translated, params_list)
            self.rowcount = self._cursor.rowcount

        await asyncio.to_thread(_run)
        return self

    async def executescript(self, script):
        for statement in _sync._split_statements(script):
            await self.execute(statement)
        return self

    async def fetchone(self):
        def _run():
            return self._cursor.fetchone()

        row = await asyncio.to_thread(_run)
        return _sync._build_row(self._cursor, row)

    async def fetchall(self):
        def _run():
            return self._cursor.fetchall()

        rows = await asyncio.to_thread(_run)
        return [_sync._build_row(self._cursor, r) for r in rows]

    async def fetchmany(self, size=None):
        def _run():
            return self._cursor.fetchmany(size) if size else self._cursor.fetchmany()

        rows = await asyncio.to_thread(_run)
        return [_sync._build_row(self._cursor, r) for r in rows]

    async def close(self):
        def _run():
            try:
                self._cursor.close()
            except Exception:  # noqa: BLE001
                pass

        await asyncio.to_thread(_run)


# ==================== СОЕДИНЕНИЕ ====================


class AsyncConnection:
    """Совместим с ``aiosqlite.Connection``."""

    def __init__(self, db_path="database.db", timeout=30, **_kwargs):
        self.row_factory = None
        self._db_path = db_path
        self._timeout = timeout
        self._pg = None
        self._pool = None
        self._closed = False
        self._lock = asyncio.Lock()

    # -- ленивая инициализация соединения --
    async def _ensure_ready(self):
        if self._pg is not None:
            # Test if connection is alive
            try:
                await asyncio.to_thread(self._pg.cursor().execute, "SELECT 1")
            except Exception:
                # Connection is dead, close and reconnect
                try:
                    self._pool.putconn(self._pg, close=True)
                except Exception:
                    pass
                self._pg = None
        if self._pg is not None:
            return
        pool = _get_pool()
        self._pool = pool
        self._pg = await asyncio.to_thread(pool.getconn)
        self._pg.autocommit = False

    async def _release(self):
        if self._pg is not None and self._pool is not None:
            pg = self._pg
            pool = self._pool
            self._pg = None
            await asyncio.to_thread(pool.putconn, pg)

    async def cursor(self):
        await self._ensure_ready()
        return AsyncCursor(self, self._pg.cursor())

    async def execute(self, sql, params=None):
        await self._ensure_ready()
        cur = AsyncCursor(self, self._pg.cursor())
        return await cur.execute(sql, params)

    async def executemany(self, sql, params_list):
        await self._ensure_ready()
        cur = AsyncCursor(self, self._pg.cursor())
        return await cur.executemany(sql, params_list)

    async def executescript(self, script):
        await self._ensure_ready()
        cur = AsyncCursor(self, self._pg.cursor())
        return await cur.executescript(script)

    async def commit(self):
        if self._pg is None:
            return
        await asyncio.to_thread(self._pg.commit)

    async def rollback(self):
        if self._pg is None:
            return
        await asyncio.to_thread(self._pg.rollback)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pg is not None:
            try:
                await asyncio.to_thread(self._pg.rollback)
            except Exception:  # noqa: BLE001
                pass
            await self._release()

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()

    async def __aenter__(self):
        await self._ensure_ready()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._pg is not None:
            try:
                if exc_type:
                    await self.rollback()
                else:
                    # aiosqlite не коммитит автоматически при выходе — сохраняем то же поведение.
                    await self.rollback()
            except Exception:  # noqa: BLE001
                pass
        await self.close()
        return False


# ==================== ЭКСПОРТ (aiosqlite API) ====================

connect = AsyncConnection
Row = _sync.Row
Connection = AsyncConnection
Cursor = AsyncCursor

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