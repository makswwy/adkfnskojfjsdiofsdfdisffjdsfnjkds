"""
Unified database module - работает с SQLite или PostgreSQL.
Переключение через переменную USE_POSTGRES.
"""

import os
import sqlite3
import aiosqlite
import psycopg2
import psycopg2.extras
from datetime import datetime, date
from decimal import Decimal

# ==================== НАСТРОЙКА ====================
USE_POSTGRES = os.environ.get('USE_POSTGRES', '0') == '1'

# PostgreSQL конфиг
PG_CONFIG = {
    'host': 'node1.pghost.ru',
    'port': 16182,
    'dbname': 'bothost_db_4b8532d13210',
    'user': 'bothost_db_4b8532d13210',
    'password': 'Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ'
}

# ==================== PostgreSQL Async ====================

class PgAsyncConnection:
    def __init__(self):
        self._pool = None
    
    async def _get_conn(self):
        if self._pool is None:
            self._pool = psycopg2.pool.SimpleConnectionPool(
                1, 20,
                host=PG_CONFIG['host'],
                port=PG_CONFIG['port'],
                dbname=PG_CONFIG['dbname'],
                user=PG_CONFIG['user'],
                password=PG_CONFIG['password']
            )
        return self._pool.getconn()
    
    async def _release_conn(self, conn):
        if self._pool:
            self._pool.putconn(conn)
    
    async def cursor(self):
        conn = await self._get_conn()
        return PgAsyncCursor(conn)
    
    async def commit(self):
        conn = await self._get_conn()
        conn.commit()
        await self._release_conn(conn)
    
    async def rollback(self):
        conn = await self._get_conn()
        conn.rollback()
        await self._release_conn(conn)
    
    async def close(self):
        if self._pool:
            self._pool.closeall()
            self._pool = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class PgAsyncCursor:
    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()
    
    def execute(self, query, params=None):
        if params:
            self._cursor.execute(query, params)
        else:
            self._cursor.execute(query)
    
    def executemany(self, query, params_list):
        self._cursor.executemany(query, params_list)
    
    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return self._process_row(row)
    
    def fetchall(self):
        return [self._process_row(row) for row in self._cursor.fetchall()]
    
    def fetchmany(self, size):
        return [self._process_row(row) for row in self._cursor.fetchmany(size)]
    
    def _process_row(self, row):
        if isinstance(row, dict):
            return {k: self._process_value(v) for k, v in row.items()}
        else:
            return tuple(self._process_value(v) for v in row)
    
    def _process_value(self, value):
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value
    
    @property
    def rowcount(self):
        return self._cursor.rowcount
    
    def close(self):
        self._cursor.close()


# ==================== SQLite Async ====================

class SqliteAsyncConnection:
    def __init__(self, db_path="database.db", timeout=30):
        self._db_path = db_path
        self._timeout = timeout
        self.row_factory = None
    
    async def cursor(self):
        return await aiosqlite.connect(self._db_path, timeout=self._timeout)
    
    async def commit(self):
        pass  # aiosqlite handles this
    
    async def rollback(self):
        pass
    
    async def close(self):
        pass
    
    async def __aenter__(self):
        return await aiosqlite.connect(self._db_path, timeout=self._timeout)
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# ==================== Выбор ====================

if USE_POSTGRES:
    connect_sqlite = PgAsyncConnection
    print("🌐 Используем PostgreSQL")
else:
    connect_sqlite = SqliteAsyncConnection
    print("📁 Используем SQLite")
