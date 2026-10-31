# -*- coding: utf-8 -*-
import asyncio
import database_pg_async as aiosqlite
import database_pg as sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable, TypeVar

DB_PATH = "database.db"
DB_WRITE_LOCK = asyncio.Lock()
T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=4)


def _sync_query(query: str, params=None, fetch: str = "none"):
    # Адаптер database_pg игнорирует db_path — всегда внешняя PostgreSQL.
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
        isolation_level=None,
    )
    try:
        cur = conn.cursor()
        if params is not None:
            cur.execute(query, params)
        else:
            cur.execute(query)
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        elif fetch == "rowcount":
            result = cur.rowcount
        else:
            result = None
        q_upper = query.strip().upper()
        if any(q_upper.startswith(p) for p in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE")):
            conn.commit()
        return result
    finally:
        conn.close()


async def db_query(query: str, params=None, fetch: str = "none"):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _sync_query, query, params, fetch)


async def configure_sqlite(db) -> None:
    # PostgreSQL-specific configuration removed
    pass


async def connect_sqlite():
    db = await aiosqlite.connect(DB_PATH, timeout=30)
    await configure_sqlite(db)
    return db


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    return "locked" in str(error).lower() or "timeout" in str(error).lower()


async def with_sqlite_retry(
    operation: Callable[[], Awaitable[T]],
    retries: int = 6,
    delay: float = 0.25,
) -> T:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        try:
            return await operation()
        except sqlite3.OperationalError as error:
            last_error = error
            if not _is_locked_error(error) or attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("SQLite retry loop exited without returning or raising an error")


async def run_locked_sqlite_write(
    operation: Callable[[aiosqlite.Connection], Awaitable[T]],
    retries: int = 6,
    delay: float = 0.25,
) -> T:
    async def _attempt() -> T:
        async with DB_WRITE_LOCK:
            db = await connect_sqlite()
            try:
                return await operation(db)
            finally:
                await db.close()

    return await with_sqlite_retry(_attempt, retries=retries, delay=delay)
