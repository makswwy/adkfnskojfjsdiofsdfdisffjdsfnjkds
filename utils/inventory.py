from typing import List, Dict, Optional
import database_pg_async as aiosqlite
from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite

DB_PATH = "database.db"


def _repair_mojibake_text(text: str) -> str:
    value = str(text or "")
    suspicious_markers = ("Р", "С", "Ð", "Ñ")
    if not any(marker in value for marker in suspicious_markers):
        return value

    candidates = [value]
    for encoding in ("latin1", "cp1252"):
        for candidate in list(candidates):
            try:
                repaired = candidate.encode(encoding).decode("utf-8")
                if repaired not in candidates:
                    candidates.append(repaired)
            except Exception:
                pass

    best = value
    best_score = value.count("Р") + value.count("С") + value.count("Ð") + value.count("Ñ")
    for candidate in candidates[1:]:
        score = candidate.count("Р") + candidate.count("С") + candidate.count("Ð") + candidate.count("Ñ")
        if score < best_score:
            best = candidate
            best_score = score
    return best


def _canonical_item_name(item_type: str, item_name: str, item_value: int) -> str:
    return item_name


def _normalize_inventory_item(row: Dict) -> Dict:
    item = dict(row)
    item_type = str(item.get("item_type", "") or "")
    item_value = int(item.get("item_value", 0) or 0)
    repaired_name = _repair_mojibake_text(str(item.get("item_name", "") or ""))
    item["item_name"] = _canonical_item_name(item_type, repaired_name, item_value)
    return item


def normalize_public_item(item: Dict) -> Dict:
    return _normalize_inventory_item(item)


async def get_inventory(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT id, item_type, item_name, item_value
            FROM inventory
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        return [_normalize_inventory_item(dict(row)) for row in rows]


async def add_item(user_id: int, item_type: str, item_name: str, item_value: int = 0) -> None:
    clean_name = _repair_mojibake_text(item_name)
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "INSERT INTO inventory (user_id, item_type, item_name, item_value) VALUES (?, ?, ?, ?)",
                (user_id, item_type, clean_name, item_value),
            )
            await db.commit()
        finally:
            await db.close()


async def add_items_bulk(user_id: int, item_type: str, item_name: str, item_value: int = 0, quantity: int = 1) -> None:
    quantity = max(1, int(quantity or 1))
    clean_name = _repair_mojibake_text(item_name)
    rows = [(int(user_id), item_type, clean_name, int(item_value or 0)) for _ in range(quantity)]
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.executemany(
                "INSERT INTO inventory (user_id, item_type, item_name, item_value) VALUES (?, ?, ?, ?)",
                rows,
            )
            await db.commit()
        finally:
            await db.close()


async def get_item_by_id(user_id: int, item_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, item_type, item_name, item_value FROM inventory WHERE user_id = ? AND id = ?",
            (user_id, item_id),
        )
        row = await cur.fetchone()
        return _normalize_inventory_item(dict(row)) if row else None


async def remove_item(user_id: int, item_id: int) -> None:
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute("DELETE FROM inventory WHERE user_id = ? AND id = ?", (user_id, item_id))
            await db.commit()
        finally:
            await db.close()


async def take_item_by_id(user_id: int, item_id: int) -> Optional[Dict]:
    item = await get_item_by_id(user_id, item_id)
    if not item:
        return None
    await remove_item(user_id, item_id)
    return item


async def take_all_items(user_id: int) -> List[Dict]:
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT id, item_type, item_name, item_value
                FROM inventory
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            )
            rows = await cur.fetchall()
            if not rows:
                return []
            await db.execute(
                """
                DELETE FROM inventory
                WHERE user_id = ?
                """,
                (user_id,),
            )
            await db.commit()
            return [_normalize_inventory_item(dict(row)) for row in rows]
        finally:
            await db.close()


async def apply_item_effect(user_id: int, item: Dict) -> int:
    # Талисманы и бонусы к доходу отключены
    return 0





async def get_prize_bonus_percent(user_id: int) -> int:
    items = await get_inventory(user_id)
    total = 0
    for item in items:
        if str(item.get("item_type", "")).lower() == "prize_bonus":
            total += int(item.get("item_value", 0) or 0)
    return total
