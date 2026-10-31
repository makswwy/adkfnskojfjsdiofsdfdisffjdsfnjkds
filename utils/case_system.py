import json
import random
import database_pg as sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import database_pg_async as aiosqlite

from utils.business import BUSINESSES_CATALOG, PURCHASEABLE_BUSINESS_KEYS, add_business
from utils.economy_dynamic import get_daily_economy_settings, round_economy_amount
from utils.inventory import add_item, add_items_bulk
from utils.json_store import load as _json_store_load
from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite

DB_PATH = "database.db"
BALANCES_FILE = "balances.json"
CASE_ECONOMY_CACHE_TTL_SECONDS = 5
_CASE_ECONOMY_BASE_CACHE = {"value": None, "expires_at": 0.0}
_CASE_ECONOMY_SETTINGS_CACHE = {"value": None, "expires_at": 0.0}

CASE_DEFS = {
    "daily": {"name": "Ежедневный кейс", "money_cost": 0, "banana_cost": 0, "daily": True},
    "homeless": {"name": "Кейс Бомжа", "money_cost": 7_500_000, "banana_cost": 0, "daily": False},
    "standard": {"name": "Стандартный кейс", "money_cost": 30_000_000, "banana_cost": 0, "daily": False},
    "special": {"name": "Особый кейс", "money_cost": 0, "banana_cost": 3_000, "daily": False},
    "victory_day": {"name": "Кейс День Победы", "money_cost": 225_000_000, "banana_cost": 1_500, "daily": False},
    "summer_mayhem": {"name": "Кейс Летний переполох", "money_cost": 30_000_000, "banana_cost": 1_000, "daily": False},
    "black_point": {"name": "Кейс BLACK POINT", "money_cost": 75_000_000, "banana_cost": 2_500, "daily": False},
    "admin": {"name": "Админ кейс", "epoints_cost": 300, "daily": False},
}

BASE_CASE_COSTS = {
    case_type: int(case_def.get("money_cost", 0) or 0)
    for case_type, case_def in CASE_DEFS.items()
}

CASE_ECONOMY_PROFILES = {
    "daily": {"cost": 0, "money_min": 0.15, "money_max": 0.6},
    "homeless": {"cost": 3, "money_min": 1.0, "money_max": 4.0},
    "standard": {"cost": 10, "money_min": 4.0, "money_max": 12.0},
    "summer_mayhem": {"cost": 8, "money_min": 3.0, "money_max": 10.0},
    "victory_day": {"cost": 18, "money_min": 8.0, "money_max": 22.0},
    "black_point": {"cost": 15, "money_min": 6.0, "money_max": 18.0},
    "special": {"cost": 0, "money_min": 2.0, "money_max": 6.0},
    "admin": {"cost": 0, "money_min": 10000.0, "money_max": 20000.0, "fixed_range": True},
}

# Лимиты бизнес-наград считаются от актуальной динамической экономики.
# Они специально консервативны: кейсы дают только стартовые/средние бизнесы
# и не могут открыть пользователю верхушку каталога за одну удачную прокрутку.
CASE_BUSINESS_PROFILES = {
    "homeless": {"prize_price": 10, "case_cost_price": 6, "prize_income": 3},
    "standard": {"prize_price": 25, "case_cost_price": 6, "prize_income": 6},
    "special": {"prize_price": 35, "case_cost_price": 0, "prize_income": 8},
    "summer_mayhem": {"prize_price": 45, "case_cost_price": 6, "prize_income": 10},
    "victory_day": {"prize_price": 60, "case_cost_price": 6, "prize_income": 14},
    "black_point": {"prize_price": 80, "case_cost_price": 8, "prize_income": 18},
}


BLACK_POINT_ITEM_REWARDS = []

ADMIN_CASE_ITEM_REWARDS = []


def _load_balances_for_case_economy() -> Dict:
    # Балансы хранятся во внешней PostgreSQL (таблица json_store).
    data = _json_store_load(BALANCES_FILE, default=None)
    return data if isinstance(data, dict) else {}


def _economy_total_for_case_balance(balance: Dict) -> int:
    return max(0, int(balance.get("wallet", 0) or 0) + int(balance.get("bank", 0) or 0))


def _global_role_user_ids() -> set[str]:
    try:
        with sqlite3.connect(DB_PATH) as db:
            rows = db.execute("SELECT user_id FROM global_managers WHERE level > 0").fetchall()
            return {str(int(row[0])) for row in rows}
    except Exception:
        return set()


def _top_average_for_case_economy(limit: int = 10) -> int:
    balances = _load_balances_for_case_economy()
    excluded_users = _global_role_user_ids()
    totals = sorted(
        (
            _economy_total_for_case_balance(balance)
            for user_id, balance in balances.items()
            if isinstance(balance, dict) and str(user_id) not in excluded_users
        ),
        reverse=True,
    )
    top_totals = [total for total in totals[:limit] if total > 0]
    if not top_totals:
        return 0
    return sum(top_totals) // len(top_totals)


def _round_case_amount(amount: int, step: Optional[int] = None) -> int:
    return round_economy_amount(amount, step)


def _case_economy_settings() -> Dict:
    now_ts = time.time()
    cached_value = _CASE_ECONOMY_SETTINGS_CACHE.get("value")
    if isinstance(cached_value, dict) and now_ts < float(_CASE_ECONOMY_SETTINGS_CACHE.get("expires_at", 0.0) or 0.0):
        return cached_value

    settings = get_daily_economy_settings()
    if not isinstance(settings, dict):
        settings = {}
    _CASE_ECONOMY_SETTINGS_CACHE["value"] = settings
    _CASE_ECONOMY_SETTINGS_CACHE["expires_at"] = now_ts + CASE_ECONOMY_CACHE_TTL_SECONDS
    return settings


def _case_economy_base() -> int:
    now_ts = time.time()
    cached_value = _CASE_ECONOMY_BASE_CACHE.get("value")
    if cached_value is not None and now_ts < float(_CASE_ECONOMY_BASE_CACHE.get("expires_at", 0.0) or 0.0):
        return int(cached_value)
    value = int(_case_economy_settings().get("case_base", 50_000) or 50_000)
    _CASE_ECONOMY_BASE_CACHE["value"] = value
    _CASE_ECONOMY_BASE_CACHE["expires_at"] = now_ts + CASE_ECONOMY_CACHE_TTL_SECONDS
    return value


def _dynamic_case_money_range(case_type: str) -> Tuple[int, int]:
    profile = CASE_ECONOMY_PROFILES.get(case_type, CASE_ECONOMY_PROFILES["special"])
    # Для админ кейса используем фиксированный диапазон
    if str(case_type) == "admin" and profile.get("fixed_range"):
        return (10_000_000_000, 20_000_000_000)
    base = _case_economy_base()
    money_cost = int(CASE_DEFS.get(case_type, {}).get("money_cost", 0) or 0)
    if money_cost > 0:
        min_amount = _round_case_amount(int(money_cost * 0.65))
        max_amount = _round_case_amount(int(money_cost * 1.5))
        return min_amount, max(min_amount + 10_000, max_amount)
    min_amount = _round_case_amount(int(base * float(profile["money_min"])))
    max_amount = _round_case_amount(int(base * float(profile["money_max"])))
    return min_amount, max(min_amount + 10_000, max_amount)


def _format_case_money_range(case_type: str) -> str:
    min_amount, max_amount = _dynamic_case_money_range(case_type)
    return f"Игровая валюта ({min_amount:,}-{max_amount:,}₽)".replace(",", ".")


def _dynamic_case_money_reward(case_type: str) -> Dict:
    min_amount, max_amount = _dynamic_case_money_range(case_type)
    return {"type": "money", "amount": random.randint(min_amount, max_amount), "text": "Игровая валюта"}


def _case_item_reward(item_meta: Dict) -> Dict:
    return {
        "type": "item",
        "item_type": str(item_meta["item_type"]),
        "name": str(item_meta["name"]),
        "value": int(item_meta["value"]),
    }


def refresh_case_economy() -> None:
    _CASE_ECONOMY_BASE_CACHE["value"] = None
    _CASE_ECONOMY_BASE_CACHE["expires_at"] = 0.0
    _CASE_ECONOMY_SETTINGS_CACHE["value"] = None
    _CASE_ECONOMY_SETTINGS_CACHE["expires_at"] = 0.0
    base = _case_economy_base()
    for case_type, profile in CASE_ECONOMY_PROFILES.items():
        if case_type not in CASE_DEFS:
            continue
        if CASE_DEFS[case_type].get("daily"):
            CASE_DEFS[case_type]["money_cost"] = 0
            continue
        cost_multiplier = int(profile.get("cost", 0) or 0)
        if cost_multiplier > 0:
            CASE_DEFS[case_type]["money_cost"] = _round_case_amount(base * cost_multiplier)

CASE_CHANCES = {
    "daily": [
        ("Игровая валюта (150.000-1.500.000₽)", 96.0),
        ("VIP на 1 день", 4.0),
    ],
    "homeless": [
        ("Игровая валюта", 98.5),
        ("Экономически доступный стартовый бизнес", 1.5),
    ],
    "standard": [
        ("Игровая валюта", 88.0),
        ("Коины (150-1.500)", 5.0),
        ("Экономически доступный бизнес", 1.0),
        ("VIP на 30 дней", 6.0),
    ],
    "special": [
        ("Игровая валюта", 88.5),
        ("Экономически доступный бизнес", 1.5),
        ("VIP на 90 дней", 10.0),
    ],
    "victory_day": [
        ("Игровая валюта", 63.5),
        ("Коины (750-6.000)", 15.0),
        ("Экономически доступный бизнес", 1.5),
        ("VIP на 30 дней", 20.0),
    ],
    "summer_mayhem": [
        ("Игровая валюта", 67.29),
        ("Коины (450-2.800)", 14.0),
        ("VIP на 30 дней", 16.71),
        ("Экономически доступный бизнес", 2.0),
    ],
    "black_point": [
        ("Игровая валюта", 62.5),
        ("Коины (1.000-8.000)", 14.0),
        ("VIP на 30 дней", 17.0),
        ("Экономически доступный бизнес", 6.5),
    ],
    "admin": [
        ("Игровая валюта (10.000.000.000-20.000.000.000₽)", 50.0),
        ("Коины (5.000-50.000)", 20.0),
        ("VIP на 180 дней", 30.0),
    ],
}


def _format_percent(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).rstrip("0").rstrip(".")


def _build_case_chances_text() -> str:
    lines = ["Шансы выпадения по кейсам:"]
    ordered_case_types = ["daily", "homeless", "standard", "special", "summer_mayhem", "victory_day", "black_point"]
    for index, case_type in enumerate(ordered_case_types, start=1):
        lines.append("")
        lines.append(f"{index}. {CASE_DEFS[case_type]['name']}")
        for label, chance in CASE_CHANCES[case_type]:
            lines.append(f"- {label} — {_format_percent(chance)}%")
    return "\n".join(lines)


CASE_CHANCES_TEXT = _build_case_chances_text()


def get_case_chances_text(include_admin: bool = False) -> str:
    lines = ["Шансы выпадения по кейсам:"]
    ordered_case_types = ["daily", "homeless", "standard", "special", "summer_mayhem", "victory_day", "black_point"]
    if include_admin:
        ordered_case_types.append("admin")
    for index, case_type in enumerate(ordered_case_types, start=1):
        lines.append("")
        lines.append(f"{index}. {CASE_DEFS[case_type]['name']}")
        for reward_index, (label, chance) in enumerate(CASE_CHANCES[case_type]):
            if reward_index == 0:
                label = _format_case_money_range(case_type)
            lines.append(f"- {label} - {_format_percent(chance)}%")
    return "\n".join(lines)


def _business_reward_text(business_key: str) -> str:
    business_meta = BUSINESSES_CATALOG.get(business_key, {})
    return str(business_meta.get("name", business_key))


def _current_case_business_limits(case_type: str) -> Tuple[int, int]:
    profile = CASE_BUSINESS_PROFILES.get(case_type)
    if not profile:
        return 0, 0

    settings = _case_economy_settings()
    prize_base = max(1, int(settings.get("prize_base", 100_000) or 100_000))
    case_cost = max(0, int(CASE_DEFS.get(case_type, {}).get("money_cost", 0) or 0))

    price_from_prize = prize_base * int(profile.get("prize_price", 0) or 0)
    price_from_cost = case_cost * int(profile.get("case_cost_price", 0) or 0)
    max_price = max(price_from_prize, price_from_cost)
    max_income = prize_base * int(profile.get("prize_income", 0) or 0)
    return max(0, int(max_price)), max(0, int(max_income))


def _get_case_business_keys(case_type: str) -> List[str]:
    max_price, max_income = _current_case_business_limits(case_type)
    if max_price <= 0 or max_income <= 0:
        return []

    available: List[str] = []
    for key in PURCHASEABLE_BUSINESS_KEYS:
        meta = BUSINESSES_CATALOG.get(key)
        if not isinstance(meta, dict):
            continue
        price = max(0, int(meta.get("price", 0) or 0))
        income_max = max(
            int(meta.get("income_max", 0) or 0),
            int(meta.get("base_income", 0) or 0),
        )
        if 0 < price <= max_price and 0 < income_max <= max_income:
            available.append(key)
    return available


def _pick_weighted_business(case_type: str) -> Dict:
    available_keys = _get_case_business_keys(case_type)
    if not available_keys:
        return _dynamic_case_money_reward(case_type)

    max_price_limit, max_income_limit = _current_case_business_limits(case_type)
    weights: List[int] = []
    for key in available_keys:
        meta = BUSINESSES_CATALOG.get(key, {})
        price = max(1, int(meta.get("price", 1) or 1))
        income_max = max(
            1,
            int(meta.get("income_max", 0) or 0),
            int(meta.get("base_income", 0) or 0),
        )
        price_weight = max(1.0, max_price_limit / price)
        income_weight = max(1.0, max_income_limit / income_max)
        weights.append(max(1, int(min(price_weight, income_weight) * 100)))

    selected_key = random.choices(available_keys, weights=weights, k=1)[0]
    return {"type": "business", "business_key": selected_key, "text": _business_reward_text(selected_key)}


def _pick_case_reward(case_type: str) -> Dict:
    roll = random.random() * 100

    if case_type == "daily":
        if roll < 96:
            return _dynamic_case_money_reward(case_type)
        return {"type": "vip_days", "days": 1, "text": "VIP на 1 день"}

    if case_type == "homeless":
        if roll < 98.5:
            return _dynamic_case_money_reward(case_type)
        return _pick_weighted_business(case_type)

    if case_type == "standard":
        if roll < 88:
            return _dynamic_case_money_reward(case_type)
        if roll < 93:
            return {"type": "bananas", "amount": random.randint(150, 1_500), "text": "Коины"}
        if roll < 94:
            return _pick_weighted_business(case_type)
        return {"type": "vip_days", "days": 30, "text": "VIP на 30 дней"}

    if case_type == "special":
        if roll < 88.5:
            return _dynamic_case_money_reward(case_type)
        if roll < 90:
            return _pick_weighted_business(case_type)
        return {"type": "vip_days", "days": 90, "text": "VIP на 90 дней"}

    if case_type == "victory_day":
        if roll < 63.5:
            return _dynamic_case_money_reward(case_type)
        if roll < 78.5:
            return {"type": "bananas", "amount": random.randint(750, 6_000), "text": "Коины"}
        if roll < 80:
            return _pick_weighted_business(case_type)
        return {"type": "vip_days", "days": 30, "text": "VIP на 30 дней"}

    if case_type == "summer_mayhem":
        if roll < 67.29:
            return _dynamic_case_money_reward(case_type)
        if roll < 81.29:
            return {"type": "bananas", "amount": random.randint(450, 2_800), "text": "Коины"}
        if roll < 89.29:
            return {"type": "vip_days", "days": 30, "text": "VIP на 30 дней"}
        if roll < 90.54:
            return _pick_weighted_business(case_type)
        return {"type": "bananas", "amount": random.randint(2800, 5000), "text": "Коины"}

    if case_type == "black_point":
        if roll < 62.5:
            return _dynamic_case_money_reward(case_type)
        if roll < 76.5:
            return {"type": "bananas", "amount": random.randint(1_000, 8_000), "text": "Коины"}
        if roll < 83.5:
            return {"type": "vip_days", "days": 30, "text": "VIP на 30 дней"}
        if roll < 85.5:
            return _pick_weighted_business(case_type)
        return {"type": "bananas", "amount": random.randint(8000, 15000), "text": "Коины"}

    if case_type == "admin":
        if roll < 50:
            return _dynamic_case_money_reward(case_type)
        if roll < 70:
            return {"type": "bananas", "amount": random.randint(5_000, 50_000), "text": "Коины"}
        return {"type": "vip_days", "days": 180, "text": "VIP на 180 дней"}

    # Default case
    if roll < 88.5:
        return _dynamic_case_money_reward(case_type)
    if roll < 90:
        return _pick_weighted_business(case_type)
    return {"type": "vip_days", "days": 90, "text": "VIP на 90 дней"}


async def get_daily_remaining(user_id: int) -> Optional[timedelta]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT created_at FROM cases_log WHERE user_id = ? AND case_type = 'daily' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    last = datetime.fromisoformat(str(row[0]).replace(" ", "T"))
    next_time = last + timedelta(days=1)
    now = datetime.now(timezone.utc) if last.tzinfo else datetime.now()
    if now >= next_time:
        return None
    return next_time - now


async def log_case_open(user_id: int, case_type: str, reward_type: str, reward_value: str) -> None:
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "INSERT INTO cases_log (user_id, case_type, reward_type, reward_value) VALUES (?, ?, ?, ?)",
                (user_id, case_type, reward_type, reward_value),
            )
            await db.commit()
        finally:
            await db.close()


async def add_user_case(user_id: int, case_type: str) -> int:
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            cur = await db.execute(
                "INSERT INTO user_cases (user_id, case_type) VALUES (?, ?)",
                (user_id, case_type),
            )
            await db.commit()
            return int(cur.lastrowid)
        finally:
            await db.close()


async def add_user_cases_bulk(user_id: int, case_type: str, quantity: int) -> List[int]:
    quantity = max(1, int(quantity or 1))
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            case_ids = []
            for _ in range(quantity):
                cur = await db.execute(
                    "INSERT INTO user_cases (user_id, case_type) VALUES (?, ?) RETURNING id",
                    (user_id, case_type),
                )
                row = await cur.fetchone()
                if row:
                    case_ids.append(int(row[0]))
            await db.commit()
        finally:
            await db.close()
    return case_ids


async def get_user_cases(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, case_type, created_at FROM user_cases WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["meta"] = CASE_DEFS.get(row["case_type"], {"name": row["case_type"]})
    return rows


async def get_opened_cases_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM cases_log WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_user_case_by_id(user_id: int, case_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, case_type, created_at FROM user_cases WHERE user_id = ? AND id = ?",
            (user_id, case_id),
        )
        row = await cur.fetchone()
    if not row:
        return None
    result = dict(row)
    result["meta"] = CASE_DEFS.get(result["case_type"], {"name": result["case_type"]})
    return result


async def remove_user_case(user_id: int, case_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_cases WHERE user_id = ? AND id = ?", (user_id, case_id))
        await db.commit()


async def remove_user_cases_bulk(user_id: int, case_ids: List[int]) -> None:
    case_ids = [int(case_id) for case_id in case_ids if int(case_id) > 0]
    if not case_ids:
        return
    placeholders = ",".join("?" for _ in case_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"DELETE FROM user_cases WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *case_ids],
        )
        await db.commit()


async def open_case(case_type: str, user_id: int) -> Tuple[Dict, str]:
    reward = _pick_case_reward(case_type)
    message = ""
    if reward["type"] == "item":
        await add_item(user_id, reward["item_type"], reward["name"], reward["value"])
        message = reward["name"]
    elif reward["type"] == "business":
        try:
            await add_business(user_id, reward["business_key"])
            message = reward["text"]
        except ValueError:
            reward = _dynamic_case_money_reward(case_type)
            message = f'{int(reward["amount"]):,}₽'.replace(",", ".")
    elif reward["type"] == "money":
        message = f'{reward["amount"]:,}₽'.replace(",", ".")
    elif reward["type"] == "bananas":
        message = f'{reward["amount"]:,} коинов'.replace(",", ".")
    elif reward["type"] == "vip_days":
        message = reward["text"]
    await log_case_open(user_id, case_type, reward["type"], message)
    return reward, message


async def open_cases_bulk(case_type: str, user_id: int, quantity: int) -> List[Tuple[Dict, str]]:
    quantity = max(1, int(quantity or 1))
    # Проверка на максимальное количество одновременного открытия (50)
    if quantity > 50:
        quantity = 50
    results: List[Tuple[Dict, str]] = []
    item_groups: Dict[Tuple[str, str, int], int] = {}
    log_rows = []

    for _ in range(quantity):
        reward = _pick_case_reward(case_type)
        message = ""
        if reward["type"] == "item":
            message = reward["name"]
            key = (reward["item_type"], reward["name"], int(reward["value"]))
            item_groups[key] = item_groups.get(key, 0) + 1
        elif reward["type"] == "business":
            try:
                await add_business(user_id, reward["business_key"])
                message = reward["text"]
            except ValueError:
                reward = _dynamic_case_money_reward(case_type)
                message = f'{int(reward["amount"]):,}₽'.replace(",", ".")
        elif reward["type"] == "money":
            message = f'{reward["amount"]:,}₽'.replace(",", ".")
        elif reward["type"] == "bananas":
            message = f'{reward["amount"]:,} коинов'.replace(",", ".")
        elif reward["type"] == "vip_days":
            message = reward["text"]
        results.append((reward, message))
        log_rows.append((user_id, case_type, reward["type"], message))

    for (item_type, item_name, item_value), item_quantity in item_groups.items():
        await add_items_bulk(user_id, item_type, item_name, item_value, item_quantity)

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.executemany(
                "INSERT INTO cases_log (user_id, case_type, reward_type, reward_value) VALUES (?, ?, ?, ?)",
                log_rows,
            )
            await db.commit()
        finally:
            await db.close()

    return results
