import json
import database_pg as sqlite3
from datetime import datetime
from typing import Dict, Optional

import pytz

from utils.json_store import load as _json_store_load, save as _json_store_save

BALANCES_FILE = "balances.json"
DB_PATH = "database.db"
DAILY_ECONOMY_FILE = "economy_daily_settings.json"
MSK_TZ = pytz.timezone("Europe/Moscow")

BASE_ECONOMY_PRIZE = 100_000
BASE_ECONOMY_VIP_PRIZE = 70_000
BASE_ECONOMY_SUBSCRIPTION = 100_000
DAILY_ECONOMY_SETTINGS_VERSION = 11
NORMAL_ECONOMY_TOP_LIMIT = 10_000_000_000
NORMAL_PRIZE_TOP_DIVISOR = 20_000
NORMAL_CASE_PRIZE_DIVISOR = 4
ECONOMY_SCALE_START_DIGITS = 9
PRIZE_DIGIT_STEP = 75_000
HUGE_CASE_PRIZE_DIVISOR = 4

# После каждого запуска процесса первый запрос обязан пересчитать экономику.
# Иначе дневной кэш может сохранить завышенные значения из прошлого запуска.
_PROCESS_ECONOMY_REFRESH_DONE = False


def _now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def _today_key(now: Optional[datetime] = None) -> str:
    current = now or _now_msk()
    if current.tzinfo is None:
        current = MSK_TZ.localize(current)
    return current.astimezone(MSK_TZ).date().isoformat()


def _load_json_file(path: str) -> Dict:
    # Данные хранятся во внешней PostgreSQL (таблица json_store).
    data = _json_store_load(path, default=None)
    return data if isinstance(data, dict) else {}


def _save_json_file(path: str, data: Dict) -> None:
    _json_store_save(path, data)


def _economy_user_total(balance_data: Dict) -> int:
    return max(0, int(balance_data.get("wallet", 0) or 0) + int(balance_data.get("bank", 0) or 0))


def _global_role_user_ids() -> set[str]:
    try:
        with sqlite3.connect(DB_PATH) as db:
            rows = db.execute("SELECT user_id FROM global_managers WHERE level > 0").fetchall()
            return {str(int(row[0])) for row in rows}
    except Exception:
        return set()


def _top_economy_average(limit: int = 10) -> int:
    balances = _load_json_file(BALANCES_FILE)
    excluded_users = _global_role_user_ids()
    totals = sorted(
        (
            _economy_user_total(balance)
            for user_id, balance in balances.items()
            if isinstance(balance, dict) and str(user_id) not in excluded_users
        ),
        reverse=True,
    )
    top_totals = [total for total in totals[:limit] if total > 0]
    if not top_totals:
        return 0
    if len(top_totals) >= 5:
        top_totals = top_totals[1:-1]
    elif len(top_totals) >= 3:
        top_totals = top_totals[1:]
    return sum(top_totals) // len(top_totals)


def round_economy_amount(amount: int, step: Optional[int] = None) -> int:
    amount = int(amount or 0)
    if step is None:
        if amount < 1_000_000:
            step = 10_000
        elif amount < 10_000_000:
            step = 100_000
        else:
            step = 1_000_000
    step = max(1, int(step or 1))
    return max(step, int(round(amount / step)) * step)


def _scaled_prize_base(average_top_total: int) -> int:
    if average_top_total <= 0:
        return BASE_ECONOMY_PRIZE
    if average_top_total <= NORMAL_ECONOMY_TOP_LIMIT:
        return round_economy_amount(average_top_total // NORMAL_PRIZE_TOP_DIVISOR)
    economy_digits = len(str(int(average_top_total)))
    scale = max(1, economy_digits - ECONOMY_SCALE_START_DIGITS)
    normal_ceiling_base = NORMAL_ECONOMY_TOP_LIMIT // NORMAL_PRIZE_TOP_DIVISOR
    scaled_base = BASE_ECONOMY_PRIZE + PRIZE_DIGIT_STEP * (scale ** 2)
    return round_economy_amount(max(normal_ceiling_base, scaled_base))


def _compute_daily_settings(date_key: str) -> Dict:
    average_top_total = _top_economy_average()
    prize_base = max(BASE_ECONOMY_PRIZE, _scaled_prize_base(average_top_total))
    case_divisor = NORMAL_CASE_PRIZE_DIVISOR if average_top_total <= NORMAL_ECONOMY_TOP_LIMIT else HUGE_CASE_PRIZE_DIVISOR
    case_base = max(25_000, round_economy_amount(prize_base // case_divisor))

    prize_min = prize_base
    prize_max = max(prize_min + 10_000, round_economy_amount(int(prize_min * 1.2)))

    vip_min = max(BASE_ECONOMY_VIP_PRIZE, round_economy_amount(int(prize_base * 1.15)))
    vip_max = max(vip_min + 10_000, round_economy_amount(int(vip_min * 1.2)))

    subscription = max(BASE_ECONOMY_SUBSCRIPTION, round_economy_amount(int(prize_base * 1.1)))

    return {
        "version": DAILY_ECONOMY_SETTINGS_VERSION,
        "date": date_key,
        "average_top_total": int(average_top_total),
        "prize_base": int(prize_base),
        "prize_min": int(prize_min),
        "prize_max": int(prize_max),
        "vip_prize_min": int(vip_min),
        "vip_prize_max": int(vip_max),
        "subscription_reward": int(subscription),
        "case_base": int(case_base),
    }


def get_daily_economy_settings(now: Optional[datetime] = None, force_refresh: bool = False) -> Dict:
    global _PROCESS_ECONOMY_REFRESH_DONE

    date_key = _today_key(now)
    current = _load_json_file(DAILY_ECONOMY_FILE)

    # Первый вызов после запуска всегда пересчитывает настройки по актуальному
    # состоянию балансов. Это сбрасывает устаревшие завышенные значения,
    # даже если значительные изменения и перезапуск произошли в тот же день.
    must_refresh_after_restart = not _PROCESS_ECONOMY_REFRESH_DONE

    if (
        not force_refresh
        and not must_refresh_after_restart
        and current.get("date") == date_key
        and int(current.get("version", 0) or 0) >= DAILY_ECONOMY_SETTINGS_VERSION
    ):
        return current

    settings = _compute_daily_settings(date_key)
    settings["refreshed_at"] = _now_msk().isoformat()
    _save_json_file(DAILY_ECONOMY_FILE, settings)
    _PROCESS_ECONOMY_REFRESH_DONE = True
    return settings
