import os, re
import html
import mimetypes
import json
import time
import urllib
import shutil
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Optional, Dict, Tuple, List
import tempfile



Image = None
ImageDraw = None
ImageFont = None
ImageFilter = None
ImageChops = None


from datetime import datetime, timedelta
from obnova.mutelog import mutelogs_command

import random
import math
import asyncio
import yaml
import pymysql.cursors
import logging
import aiohttp
import database_pg_async as aiosqlite
import certifi
import ssl

from vkbottle.bot import Bot, Message, rules
from vkbottle import API, Keyboard, Callback, KeyboardButtonColor, Text, GroupEventType, GroupTypes, User
from vkbottle.http.aiohttp import SingleAiohttpClient
import database_pg as sqlite3
import sys
import inspect
import subprocess

import pytz
from utils.db import init_economy_schema
from utils.runtime_encoding import configure_utf8_stdio


from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite


from utils.admin_stats import log_admin_action, calculate_and_set_norm, get_total_epoints, spend_epoints
configure_utf8_stdio()


MSK_TZ = pytz.timezone("Europe/Moscow")
RECENT_CHAT_EVENTS_TTL_SECONDS = 8
recent_chat_events: dict[tuple[int, int], float] = {}
uploaded_message_photo_cache: dict[str, str] = {}
uploaded_message_photo_locks: dict[str, asyncio.Lock] = {}
REGISTERED_CHATS_CACHE_TTL_SECONDS = 90
REGISTERED_CHAT_CHECK_CONCURRENCY = 6
REGISTERED_CHAT_CHECK_TIMEOUT_SECONDS = 6
registered_chats_cache: dict[str, object] = {"expires_at": 0.0, "active_rows": [], "inactive_rows": []}
registered_chats_cache_lock = asyncio.Lock()
VK_ENTITY_NAME_CACHE_TTL_SECONDS = 300
vk_entity_name_cache: dict[int, tuple[str, float]] = {}
_reset_cooldowns: dict[str, float] = {}
property_exchange_drafts: dict[int, dict] = {}
promo_creations: dict[int, dict] = {}
runtime_schema_ready = False
runtime_schema_lock = asyncio.Lock()
MAX_ADMIN_ITEM_GRANT_QUANTITY = 1000
MAX_ADMIN_CASE_GRANT_QUANTITY = 1000
PROMO_RESERVED_PREFIXES = ("/", "!", "+")


def msk_now() -> datetime:
    return datetime.now(MSK_TZ)


def msk_now_str() -> str:
    return msk_now().strftime("%Y-%m-%d %H:%M:%S")


async def send_staff_feedback_message(
    message_text: str,
    source_user_id: int = 0,
    feedback_type: str = "feedback",
) -> tuple[bool, str]:
    errors: list[str] = []

    admin_ids: list[int] = []
    for candidate in [int(bansids or 0), 488828183, 574393629]:
        if candidate > 0 and candidate not in admin_ids:
            admin_ids.append(candidate)

    delivered_to_admin = False
    for admin_id in admin_ids:
        try:
            await bot.api.messages.send(
                peer_id=admin_id,
                random_id=0,
                message=message_text,
                disable_mentions=1,
            )
            delivered_to_admin = True
        except Exception as error:
            errors.append(f"user{admin_id}:{error}")
            print(f"[staff_feedback] admin send error user_id={admin_id}: {error}")

    if delivered_to_admin:
        return True, ""

    try:
        sql.execute(
            "INSERT INTO bugsusers (user_id, bug, datetime, bug_counts_user) VALUES (?, ?, ?, ?)",
            (int(source_user_id or 0), f"[{feedback_type}] {message_text}", msk_now_str(), 1),
        )
        database.commit()
        print(f"[staff_feedback] saved to local DB as fallback ({feedback_type})")
        return True, ""
    except Exception as error:
        errors.append(f"db:{error}")
        print(f"[staff_feedback] db fallback error: {error}")

    return False, " | ".join(errors) if errors else "Не удалось доставить сообщение"


async def record_pass_progress(user_id: int, event_type: str, amount: int = 1, bot=None):
    try:
        return await asyncio.wait_for(
            _record_pass_progress_impl(user_id, event_type, amount, bot),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        print(f"[PASS PROGRESS TIMEOUT] user_id={user_id} event={event_type} amount={amount}")
    except Exception as error:
        print(f"[PASS PROGRESS ERROR] user_id={user_id} event={event_type}: {error}")
    return []


CASE_DEFS = {}
BUSINESSES_CATALOG = {}
VICTORY_DAY_CASE_DEADLINE = MSK_TZ.localize(datetime(2026, 5, 11, 11, 0, 0))
CASE_DEFS.setdefault("victory_day", {}).update({"money_cost": 225_000_000, "banana_cost": 1_500})
SUMMER_MAYHEM_CASE_START = MSK_TZ.localize(datetime(2026, 5, 17, 13, 0, 0))
SUMMER_MAYHEM_CASE_END = MSK_TZ.localize(datetime(2026, 6, 21, 13, 0, 0))
VIP_EXCHANGE_EVENT_START = MSK_TZ.localize(datetime(2026, 5, 16, 11, 0, 0))
VIP_EXCHANGE_EVENT_END = MSK_TZ.localize(datetime(2026, 5, 18, 11, 0, 0))
LOTTERY_EVENT_START = MSK_TZ.localize(datetime(2026, 5, 20, 0, 0, 0))
LOTTERY_EVENT_END = MSK_TZ.localize(datetime(2026, 5, 23, 11, 0, 0))
LOTTERY_EVENT_COST = 1_000_000_000

BASE_ECONOMY_PRIZE = 50_000
BASE_ECONOMY_VIP_PRIZE = 70_000
BASE_ECONOMY_SUBSCRIPTION = 100_000
OLD_ECONOMY_PRIZE_BASE = 4_200_000
LIMITED_CASE_TYPES = {"victory_day", "summer_mayhem", "black_point"}
LIMITED_CASE_PROTECTED_PRIZE_VALUES = {75, 100, 175, 250, 300, 350, 500, 1000, 2500, 5000}
LIMITED_CASE_PROTECTED_BUSINESS_VALUES = {750, 1000, 1500, 2000, 2500, 3500, 5000, 10000, 15000, 25000}
ECONOMY_BUSINESS_BASE_VALUES = {
    key: {
        field: int(meta.get(field, 0) or 0)
        for field in ("price", "base_income", "income_min", "income_max")
    }
    for key, meta in BUSINESSES_CATALOG.items()
}
ECONOMY_CASE_BASE_COSTS = {
    key: int(meta.get("money_cost", 0) or 0)
    for key, meta in CASE_DEFS.items()
}

SEASONAL_EVENTS = {}


def is_victory_day_case_available() -> bool:
    return msk_now() < VICTORY_DAY_CASE_DEADLINE


def get_summer_mayhem_case_state() -> str:
    now = msk_now()
    if now < SUMMER_MAYHEM_CASE_START:
        return "upcoming"
    if now >= SUMMER_MAYHEM_CASE_END:
        return "ended"
    return "active"


def _format_compact_duration(delta: Optional[timedelta]) -> str:
    if not delta:
        return "0с"
    total_seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours or days:
        parts.append(f"{hours}ч")
    if minutes or hours or days:
        parts.append(f"{minutes}м")
    parts.append(f"{seconds}с")
    return " ".join(parts)


def _format_msk_event_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = MSK_TZ.localize(dt)
    return dt.astimezone(MSK_TZ).strftime("%d.%m.%Y %H:%M")


def _get_event_state(event_def: dict) -> str:
    now_dt = msk_now()
    start_dt = event_def["start"]
    end_dt = event_def["end"]
    if now_dt < start_dt:
        return "upcoming"
    if now_dt >= end_dt:
        return "ended"
    return "active"


def _get_event_remaining_delta(event_def: dict) -> Optional[timedelta]:
    now_dt = msk_now()
    state = _get_event_state(event_def)
    if state == "upcoming":
        return event_def["start"] - now_dt
    if state == "active":
        return event_def["end"] - now_dt
    return None


def _get_visible_events() -> list[tuple[str, dict]]:
    visible = []
    for event_id, event_def in SEASONAL_EVENTS.items():
        if _get_event_state(event_def) != "ended":
            visible.append((event_id, event_def))
    visible.sort(key=lambda item: item[1]["start"])
    return visible


def _get_vip_remaining_delta(balance_data: dict) -> Optional[timedelta]:
    vip_until = balance_data.get("vip_until")
    if not vip_until:
        return None
    try:
        until_dt = datetime.fromisoformat(str(vip_until))
    except Exception:
        return None
    now_dt = datetime.now()
    if until_dt <= now_dt:
        return None
    return until_dt - now_dt


def _format_vip_remaining_text(balance_data: dict) -> str:
    vip_delta = _get_vip_remaining_delta(balance_data)
    if not vip_delta:
        return "отсутствует"
    return _format_compact_duration(vip_delta)


def _get_event_claims_store(balance_data: dict) -> dict:
    claims = balance_data.get("event_claims")
    if not isinstance(claims, dict):
        claims = {}
        balance_data["event_claims"] = claims
    return claims


def _get_event_bonus_storage_key(event_id: str, bonus_def: dict) -> str:
    return f"{event_id}:{bonus_def.get('id', 'bonus')}"


def _parse_event_claim_dt(raw_value) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value))
    except Exception:
        return None
    if parsed.tzinfo is None:
        try:
            return MSK_TZ.localize(parsed)
        except Exception:
            return None
    return parsed.astimezone(MSK_TZ)


def _get_event_bonus_ready_at(balance_data: dict, event_id: str, bonus_def: dict) -> Optional[datetime]:
    last_claim_dt = _parse_event_claim_dt(_get_event_claims_store(balance_data).get(_get_event_bonus_storage_key(event_id, bonus_def)))
    if not last_claim_dt:
        return None
    cooldown_hours = int(bonus_def.get("cooldown_hours", 0) or 0)
    return last_claim_dt + timedelta(hours=cooldown_hours)


def _get_event_bonus_remaining_delta(balance_data: dict, event_id: str, bonus_def: dict) -> Optional[timedelta]:
    ready_at = _get_event_bonus_ready_at(balance_data, event_id, bonus_def)
    if ready_at is None:
        return None
    now_dt = msk_now()
    if ready_at <= now_dt:
        return None
    return ready_at - now_dt


def _find_event_offer(event_def: dict, offer_id: str) -> Optional[dict]:
    for offer in event_def.get("offers", []):
        if str(offer.get("id")) == str(offer_id):
            return offer
    return None


def _build_event_item_meta(item_type: str, item_name: str, item_value: int) -> dict:
    normalized = normalize_public_item(
        {
            "id": 0,
            "item_type": item_type,
            "item_name": item_name,
            "item_value": int(item_value or 0),
        }
    )
    return {
        "item_type": str(normalized.get("item_type", item_type)),
        "item_name": str(normalized.get("item_name", item_name)),
        "item_value": int(normalized.get("item_value", item_value) or 0),
    }


def _build_lottery_item_pool() -> list[dict]:
    unique_items: dict[tuple[str, str, int], dict] = {}

    def register_item(item_type: str, item_name: str, item_value: int) -> None:
        item_meta = _build_event_item_meta(item_type, item_name, item_value)
        item_key = (
            item_meta["item_type"],
            item_meta["item_name"],
            item_meta["item_value"],
        )
        if item_key not in unique_items:
            unique_items[item_key] = item_meta

    for item in ADMIN_GRANTABLE_ITEMS:
        register_item(
            str(item.get("item_type", "")),
            str(item.get("item_name", "")),
            int(item.get("item_value", 0) or 0),
        )

    for seasonal_item in SEASON_ITEM_POOL.values():
        if not isinstance(seasonal_item, (list, tuple)) or len(seasonal_item) < 3:
            continue
        register_item(
            str(seasonal_item[0]),
            str(seasonal_item[1]),
            int(seasonal_item[2] or 0),
        )

    for reward_map in (STANDARD_PASS_REWARDS, PREMIUM_PASS_REWARDS):
        for reward in reward_map.values():
            if str(reward.get("type", "")).lower() != "item":
                continue
            register_item(
                str(reward.get("item_type", "")),
                str(reward.get("item_name", "")),
                int(reward.get("item_value", 0) or 0),
            )

    for quest in POST_PASS_SPECIAL_QUEST_POOL.values():
        reward = quest.get("reward")
        if not isinstance(reward, dict) or str(reward.get("type", "")).lower() != "item":
            continue
        register_item(
            str(reward.get("item_type", "")),
            str(reward.get("item_name", "")),
            int(reward.get("item_value", 0) or 0),
        )

    return sorted(
        unique_items.values(),
        key=lambda item: (
            int(item.get("item_value", 0) or 0),
            str(item.get("item_type", "")),
            str(item.get("item_name", "")),
        ),
    )


def _pick_lottery_money_reward() -> dict:
    money_tiers = [
        (100_000, 5_000_000, 58),
        (5_000_001, 25_000_000, 24),
        (25_000_001, 100_000_000, 12),
        (100_000_001, 500_000_000, 5),
        (500_000_001, 1_500_000_000, 1),
    ]
    min_amount, max_amount, _weight = random.choices(
        money_tiers,
        weights=[tier[2] for tier in money_tiers],
        k=1,
    )[0]
    return {"type": "money", "amount": random.randint(min_amount, max_amount)}


def _pick_lottery_bananas_reward() -> dict:
    banana_tiers = [
        (100, 160, 55),
        (161, 280, 30),
        (281, 400, 10),
        (401, 500, 5),
    ]
    min_amount, max_amount, _weight = random.choices(
        banana_tiers,
        weights=[tier[2] for tier in banana_tiers],
        k=1,
    )[0]
    return {"type": "bananas", "amount": random.randint(min_amount, max_amount)}


def _pick_lottery_case_reward() -> dict:
    case_pool = [
        ("homeless", 58),
        ("standard", 24),
        ("special", 12),
        ("summer_mayhem", 6),
    ]
    available_case_pool = [
        (case_type, weight)
        for case_type, weight in case_pool
        if case_type in CASE_DEFS and case_type not in {"daily", "victory_day"}
    ]
    if not available_case_pool:
        raise ValueError("Для Лотыреи не найден ни один доступный кейс.")
    selected_case = random.choices(
        [case_type for case_type, _weight in available_case_pool],
        weights=[weight for _case_type, weight in available_case_pool],
        k=1,
    )[0]
    return {"type": "case", "case_type": selected_case}


def _pick_lottery_item_reward() -> dict:
    item_pool = _build_lottery_item_pool()
    if not item_pool:
        raise ValueError("Для Лотыреи не найден пул предметов.")
    weights = [max(1, len(item_pool) - index) for index, _item in enumerate(item_pool)]
    selected_item = random.choices(item_pool, weights=weights, k=1)[0]
    return {
        "type": "item",
        "item_type": selected_item["item_type"],
        "item_name": selected_item["item_name"],
        "item_value": selected_item["item_value"],
    }


async def _pick_lottery_business_reward(user_id: int) -> dict:
    user_businesses = await get_user_businesses(user_id)
    branch_counts: dict[str, int] = {}
    for business in user_businesses:
        business_key = str(business.get("business_key", ""))
        branch_counts[business_key] = branch_counts.get(business_key, 0) + 1

    available_business_pool = []
    total_businesses = len(PURCHASEABLE_BUSINESS_KEYS)
    for index, business_key in enumerate(PURCHASEABLE_BUSINESS_KEYS):
        if business_key not in BUSINESSES_CATALOG:
            continue
        remaining_slots = MAX_BRANCHES_PER_BUSINESS - branch_counts.get(business_key, 0)
        if remaining_slots <= 0:
            continue
        weight = max(1, total_businesses - index)
        available_business_pool.append((business_key, remaining_slots, weight))

    if not available_business_pool:
        return _pick_lottery_money_reward()

    selected_key, remaining_slots, _weight = random.choices(
        available_business_pool,
        weights=[weight for _business_key, _remaining_slots, weight in available_business_pool],
        k=1,
    )[0]
    quantity_tiers = [
        (1, 2, 70),
        (3, 5, 18),
        (6, 10, 8),
        (11, 15, 3),
        (16, 20, 1),
    ]
    min_quantity, max_quantity, _tier_weight = random.choices(
        quantity_tiers,
        weights=[tier[2] for tier in quantity_tiers],
        k=1,
    )[0]
    quantity = random.randint(min_quantity, max_quantity)
    quantity = max(1, min(int(quantity), int(remaining_slots)))
    return {"type": "business", "business_key": selected_key, "quantity": quantity}


async def _roll_lottery_reward(user_id: int) -> dict:
    reward_type = random.choices(
        ["money", "bananas", "case", "business", "item"],
        weights=[89.5, 6.5, 2.8, 0.9, 0.3],
        k=1,
    )[0]
    if reward_type == "money":
        return _pick_lottery_money_reward()
    if reward_type == "bananas":
        return _pick_lottery_bananas_reward()
    if reward_type == "case":
        return _pick_lottery_case_reward()
    if reward_type == "business":
        return await _pick_lottery_business_reward(user_id)
    return _pick_lottery_item_reward()


def _describe_event_reward(reward: dict) -> str:
    reward_type = str(reward.get("type", "")).lower()
    if reward_type == "money":
        return f"{format_number(int(reward.get('amount', 0) or 0))}₽"
    if reward_type == "bananas":
        return f"{format_number(int(reward.get('amount', 0) or 0))} коинов"
    if reward_type == "case":
        case_type = str(reward.get("case_type", "")).lower()
        case_name = CASE_DEFS.get(case_type, {}).get("name", case_type)
        return f"кейс «{case_name}»"
    if reward_type == "business":
        business_key = str(reward.get("business_key", "")).lower()
        business_name = BUSINESSES_CATALOG.get(business_key, {}).get("name", business_key)
        quantity = max(1, int(reward.get("quantity", 1) or 1))
        if quantity > 1:
            return f"{format_number(quantity)} филиала бизнеса «{business_name}»"
        return f"бизнес «{business_name}»"
    if reward_type == "item":
        item_meta = _build_event_item_meta(
            str(reward.get("item_type", "")),
            str(reward.get("item_name", "")),
            int(reward.get("item_value", 0) or 0),
        )
        quantity = max(1, int(reward.get("quantity", 1) or 1))
        if quantity > 1:
            return f"{format_number(quantity)} предмета «{item_meta['item_name']}»"
        return f"предмет «{item_meta['item_name']}»"
    return "неизвестная награда"


def should_skip_duplicate_chat_event(peer_id: int, conversation_message_id: int) -> bool:
    now_ts = time.monotonic()
    expired_keys = [key for key, seen_at in recent_chat_events.items() if now_ts - seen_at > RECENT_CHAT_EVENTS_TTL_SECONDS]
    for key in expired_keys:
        recent_chat_events.pop(key, None)

    event_key = (int(peer_id), int(conversation_message_id))
    if event_key in recent_chat_events:
        return True

    recent_chat_events[event_key] = now_ts
    return False


GBAN_SPECIAL_CHAT_TYPES = {"hel", "ld", "adm", "mod", "tex", "test", "med", "ruk"}

BOT_RULES_TEXT = (
    "🌐 Глобальные правила ADMIN MANAGER\n\n"
    "Ниже перечислены нарушения, за которые могут выдаваться глобальные наказания:\n"
    "• ЧС бота — запрет использования команд бота.\n"
    "• ЧС игр — запрет на участие в игровых беседах и использование игровых механик.\n"
    "• Глобальная блокировка — блокировка в чатах бота.\n\n"
    "Административные:\n"
    "1.1 Массовая рассылка, флуд, спам, реклама, скам и навязчивое распространение ссылок - Глобальная Блокировка.\n\n"
    "1.2 Выдача себя за руководство бота - Глобальная блокировка.\n\n"
    "1.3 Неконструктивная критика бота - Глобальная блокировка + ЧС бота.\n\n"
    "1.4 Оскорбление родни руководства - Глобальная блокировка + ЧС бота.\n\n"
    "1.5 Продажа аккаунтов с Административными/ Глобальными правами - Глобальная блокировка + ЧС бота.\n\n"
    "1.6 Ввод в заблуждение пользователей - Глобальная блокировка\n\n"
    "1.7 Слив Административного/Руководственного поста - Глобальная блокировка + ЧС бота.\n\n"
    "1.8 Ложная информация про бота - Глобальная блокировка + ЧС бота.\n\n"
    "1.9.1 Политические разговоры  - Глобальная блокировка.\n\n"
    "1.9.2 Религиозные разговоры - Глобальная блокировка.\n\n"
    "1.10 Любые виды докса, сноса, свата, призывание к этому - Глобальная блокировка + ЧС бота.\n\n"
    "1.11 Добавление сторонних сообществ - Глобальная блокировка\n\n"
    "1.12 Копирование бота - Глобальная блокировка + ЧС бота\n"
    "Исключение: Дружественные боты.\n\n"
    "1.12 Обход ранее выданных наказаний через твинков, новые аккаунты или сторонние беседы - ЧС бота + Глобальная блокировка\n\n"
    "Игровые:\n"
    "2.1 Продажа Игровой валюты - ЧС игр.\n\n"
    "2.2 Мошенничество - ЧС игр + (возможно) Глобальная блокировка\n\n"
    "2.3 Попытки обмана пользователей, обман пользователей , абуза багов, дюпа, предметов - Глобальная блокировка + ЧС игр.\n\n"
    "2.4 Продажа, покупка, обмен аккаунтов, валюты, предметов или прав вне правил - ЧС игр.\n\n"
    "2.5 Срыв работы бота: провокации на поломку, тестирование уязвимостей без разрешения - ЧС бота.\n\n"
    "2.6 Распространение вредоносного контента, фишинга, вирусов, краш-команд и опасных инструкций - Глобальная блокировка + ЧС бота.\n\n"
    "2.7 Любые действия, которые наносят вред проекту, его экономике, администрации или пользователям - ЧС бота.\n\n"
    "Тип наказания и срок определяются руководством по тяжести нарушения."
)


def is_special_gban_chat_type(chat_type: Optional[str]) -> bool:
    return (chat_type or "").lower() in GBAN_SPECIAL_CHAT_TYPES


MAX_LOGS=20
MAX_LOGS=20


p_message = 'Бот не принимает личные сообщения. Обратитесь к https://vk.com/makswwy'

with open("config.json", "r") as js:
    open_file = json.load(js)

config = open_file



class CertifiAiohttpClient(SingleAiohttpClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._session_loop = None

    @asynccontextmanager
    async def request(self, url, method="GET", data=None, **kwargs):
        current_loop = asyncio.get_running_loop()
        if (
            self.session is None
            or self.session.closed
            or self._session_loop is not current_loop
        ):
            if self.session is not None and not self.session.closed:
                await self.session.close()

            connector = aiohttp.TCPConnector(ssl=self._ssl_context)
            self.session = aiohttp.ClientSession(
                json_serialize=self.json_processing_module.dumps,
                connector=connector,
                **self._session_params,
            )
            self._session_loop = current_loop

        async with self.session.request(url=url, method=method, data=data, **kwargs) as response:
            yield response


vk_http_client = CertifiAiohttpClient()
bot = Bot(api=API(open_file['bot-token'], http_client=vk_http_client))
chatsbansgame = config['banschats']
groupid = config['group_id']
tchat = config['testers_chats']
bansids = config['form_not']

class Console:
    @staticmethod
    def log(*args):
        print(*args)

console = Console()
    

CONFIG_FILE = "config.json"
ROLES_FILE = "roles.json"
BANS_FILE = "bansoffer.json"
BANS_COMMANDS_FILE = "banscommands.json"
CASE_PURCHASE_IMAGES = {
    "daily": "photo_2026-04-29_10-34-30.jpg",
    "homeless": "photo_2026-04-29_08-27-38.jpg",
    "standard": "photo_2026-04-29_10-34-30.jpg",
    "special": "photo_2026-04-29_08-57-48.jpg",
    "victory_day": "photo_2026-05-08_21-22-45.jpg",
    "summer_mayhem": "019e34cb-4301-7e38-b6a4-1360dcf3a350.png",
    "black_point": "IMG_1282.jpeg",
    "admin": "photo_2026-06-17_07-58-39.jpg",
}
CASE_REWARD_IMAGES = {
    "daily": "photo_2026-04-29_10-37-25.jpg",
    "homeless": "photo_2026-04-29_08-27-38.jpg",
    "standard": "photo_2026-04-29_10-34-30.jpg",
    "special": "photo_2026-04-29_08-57-48.jpg",
    "victory_day": "photo_2026-05-08_21-22-45.jpg",
    "summer_mayhem": "019e34cb-4301-7e38-b6a4-1360dcf3a350.png",
    "black_point": "IMG_1282.jpeg",
    "admin": "photo_2026-06-17_07-58-39.jpg",
}


def load_banscommands():
    try:
        with open(BANS_COMMANDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_banscommands(bans):
    with open(BANS_COMMANDS_FILE, "w", encoding="utf-8") as f:
        json.dump(bans, f, ensure_ascii=False, indent=4)


def check_ban(user_id: int):
    bans = load_banscommands()
    return str(user_id) in bans

def load_bans():
    try:
        with open(BANS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_bans(bans):
    with open(BANS_FILE, "w", encoding="utf-8") as f:
        json.dump(bans, f, indent=4, ensure_ascii=False)

def is_banned(user_id: int):
    bans = load_bans()
    for ban in bans:
        if ban["user_id"] == user_id:
            return ban
    return None


def sync_balances():
    global balances
    balances = load_data(BALANCES_FILE)
    return balances
        

cmds_users = [
    "Команда1\n",
    "Команда2\n"
]

cmds_moders = [
    "Команда 1\n",
    "Команда 2\n"
]

cmds_srmoders = [
    "SRMOD1\n",
    "SRMOD2\n"
]

cmds_admins = [
    "ADMIN1\n",
    "ADMIN2\n"
]

cmds_sradmins = [
    "SRADMIN1\n",
    "SRADMIN2\n"
]

cmds_owner = [
    "OWNER1\n"
]

cmds_sa = [
    "SA1\n",
    "SA2\n"
]

cmds_zsa = [
    "ZSA1\n",
    "ZSA2\n"
]


CONFIG_FILE = "config.json"
BALANCES_FILE = "balances.json"
DICE_GAMES_FILE = "dice_games.json"
DICE_DEALERS_FILE = "dice_dealers.json"
DICE_GAME_TIMEOUT_SECONDS = 120
DICE_MIN_PLAYERS = 2
DICE_MAX_PLAYERS = 5
DICE_DEALER_COMMISSION_PERCENT = 5
DICE_BETA_MIN_GLOBAL_LEVEL = 1  
DICE_DEALER_ASSIGN_MIN_GLOBAL_LEVEL = 6  
DICE_GAME_LOCKS = {}
DICE_TIMEOUT_TASKS = {}
DICE_NAME_CACHE = {}
DICE_EDIT_LOCKS = {}
DICE_LAST_MESSAGE_HASH = {}
DICE_BUILD_VERSION = "dice-3s-stable-countdown-2026-07-19-v5"
print(f"[DICE] loaded build {DICE_BUILD_VERSION}")

DUELS_FILE = "duels.json"
GIVEAWAYS_FILE = "giveaways.json"
PRIZES_FILE = "prizes.json"
DONATES_FILE = "donates.json"
PROMO_FILE = "promo.json"
SUBS_FILE = "subs.json"
TOP_VISIBILITY_FILE = "top_visibility.json"
BANANA_OFFERS_FILE = "banana_offers.json"
WIPE_BACKUPS_DIR = "wipe_backups"
BOT_GROUP_URL = "https://vk.com/bananamanager"
SUBSCRIPTION_CHECK_TTL_SECONDS = 30
SUBSCRIPTION_NEGATIVE_CHECK_TTL_SECONDS = 5
subscription_check_cache: dict[int, tuple[bool, float]] = {}
BOT_COMMAND_PREFIXES = ("!", "+", "/")
EP_MONEY_RATE = 10_000_000
EP_BANANA_RATE = 10
EP_INPUT_TTL_SECONDS = 300
ep_shop_inputs: dict[int, dict] = {}
EP_BUSINESS_ITEMS = {
    "biz_1": {"cost": 50, "business_key": "grand_espresso", "title": "Комиссионный дом Второй Шанс"},
    "biz_2": {"cost": 150, "business_key": "golden_croissant", "title": "Бистро На Ходу"},
    "biz_3": {"cost": 500, "business_key": "gourmania", "title": "Ресторан Лунный Берег"},
}
EP_CASE_ITEMS = {
    "case_1": {"cost": 30, "case_type": "standard"},
    "case_2": {"cost": 80, "case_type": "special"},
    "case_3": {"cost": 800, "case_type": "admin"},
}
EP_ITEM_REWARDS = {
    "item_1": {"cost": 100, "item_type": "prize_bonus", "name": "Привидение (+1000% к доходу /приз)", "value": 1000, "title": "Привидение"},
    "item_2": {"cost": 250, "item_type": "prize_bonus", "name": "Админ скин (+7500% к доходу /приз)", "value": 7500, "title": "Админ скин"},
    "item_3": {"cost": 400, "item_type": "prize_bonus", "name": "Админ Амулет (+15000% к доходу /приз)", "value": 15000, "title": "Админ Амулет"},
    "item_4": {"cost": 600, "item_type": "prize_bonus", "name": "Кристалл Удачи (+25000% к доходу /приз)", "value": 25000, "title": "Кристалл Удачи"},
}
EP_VIP_ITEMS = {
    "vip_1": {"cost": 50, "days": 30},
    "vip_2": {"cost": 130, "days": 90},
    "vip_3": {"cost": 240, "days": 180},
}
    
MUTELIST_PER_PAGE = 20

def has_mute_access_sync(user_id: int, chat_id: int) -> bool:
    """Синхронная проверка прав: staff(userId,chatId) in (admin,owner,sr.administrator)
       или managers(userId).rang in (sa,zsa)"""
    global connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT rang FROM staff WHERE userId=%s AND chatId=%s", (user_id, chat_id))
            r = cursor.fetchone()
            if r and r.get("rang") in ("admin", "owner", "sr.administrator"):
                return True

            cursor.execute("SELECT rang FROM managers WHERE userId=%s", (user_id,))
            r2 = cursor.fetchone()
            if r2 and str(r2.get("rang")).lower() in ("sa", "zsa"):
                return True
                
        return False
    except Exception as e:
        print("MySQL error in has_mute_access_sync:", e)
        return False


def make_page(chats: list, page: int, per_page: int = 40) -> str:
    start = (page - 1) * per_page
    end = start + per_page
    sliced = chats[start:end]
    if not sliced:
        return "Нет чатов на этой странице."
    return "\n".join(
        [f"{i+1}. {c['chatId']} | {c['title']}" for i, c in enumerate(sliced, start=start)]
    )   
    
def get_owner_chats(user_id: int):
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT chatId, title FROM chats WHERE owner=%s ORDER BY id", (user_id,))
                return cursor.fetchall()
    except Exception as e:
        print("MySQL error in get_owner_chats:", e)
        return []

def get_mutes_sync(chat_id: int, per_page: int, offset: int):
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT userId, moder, term, reason FROM mutes WHERE chatId=%s ORDER BY id LIMIT %s OFFSET %s",
                    (chat_id, per_page, offset)
                )
                return cursor.fetchall()
    except Exception as e:
        print("MySQL error in get_mutes:", e)
        return []      
    
def _load_donates_data(file: str):
    def _pairs_hook(pairs):
        merged = {}
        for raw_key, raw_value in pairs:
            try:
                normalized_key = str(int(raw_key))
                amount = int(raw_value or 0)
            except Exception:
                continue
            merged[normalized_key] = merged.get(normalized_key, 0) + amount
        return merged

    with open(file, "r", encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=_pairs_hook)


def load_data(file):
    if os.path.exists(file):
        try:
            if os.path.basename(file) == DONATES_FILE:
                return _load_donates_data(file)
            with open(file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


for f in [BALANCES_FILE, DICE_GAMES_FILE, DICE_DEALERS_FILE, DUELS_FILE, GIVEAWAYS_FILE, PRIZES_FILE, DONATES_FILE, PROMO_FILE, SUBS_FILE, TOP_VISIBILITY_FILE, BANANA_OFFERS_FILE]:
    if not os.path.exists(f):
        with open(f, "w", encoding="utf-8") as fp:
            json.dump({}, fp)


if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("Не найден config.json! Вставь туда bot_token, admin_id и group_id")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)


balances = load_data(BALANCES_FILE)
duels = load_data(DUELS_FILE)
giveaways = load_data(GIVEAWAYS_FILE)
prizes = load_data(PRIZES_FILE)
donates = load_data(DONATES_FILE)
if donates:
    save_data(DONATES_FILE, donates)
promo = load_data(PROMO_FILE)
banana_offers = load_data(BANANA_OFFERS_FILE)

CHAT_ADMIN_CHECK_CACHE: dict[int, float] = {}
CHAT_ADMIN_CHECK_TTL_SECONDS = 45

for uid in list(balances.keys()):
    if "bananas" not in balances[uid]:
        balances[uid]["bananas"] = 0
    if "business_income_today" not in balances[uid]:
        balances[uid]["business_income_today"] = 0
    if "event_claims" not in balances[uid] or not isinstance(balances[uid].get("event_claims"), dict):
        balances[uid]["event_claims"] = {}
save_data(BALANCES_FILE, balances)


def format_number(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def get_balance(user_id: int):
    uid = str(user_id)
    if uid not in balances:
        balances[uid] = {
            "wallet": 0,
            "bank": 0,
            "won": 0,
            "lost": 0,
            "won_total": 0,
            "lost_total": 0,
            "received_total": 0,
            "sent_total": 0,
            "bananas": 0,
            "business_income_today": 0,
            "vip_until": None,
            "donated": 0,
            "event_claims": {},
        }
    if "bananas" not in balances[uid]:
        balances[uid]["bananas"] = 0
    if "business_income_today" not in balances[uid]:
        balances[uid]["business_income_today"] = 0
    if "transfers_today" not in balances[uid] or not isinstance(balances[uid].get("transfers_today"), dict):
        balances[uid]["transfers_today"] = {}
    if "event_claims" not in balances[uid] or not isinstance(balances[uid].get("event_claims"), dict):
        balances[uid]["event_claims"] = {}
    return balances[uid]


def has_unlimited_transfer_limit(role_level: int, balance_data: Optional[dict] = None) -> bool:
    """Безлимит переводов доступен только Директору бота+ и активной NOLIM.

    get_role() кодирует глобальные роли как GLOBAL_ROLE_OFFSET + global_level,
    а модифицированные роли — как MODIFIED_ROLE_OFFSET + level. Поэтому простая
    проверка ``role >= 10`` ошибочно давала безлимит почти всем модифицированным
    ролям (их значения начинаются с 200).
    """
    if isinstance(balance_data, dict):
        nolim_until = balance_data.get("nolim_until")
        if nolim_until:
            try:
                if datetime.fromisoformat(str(nolim_until)) > datetime.now():
                    return True
            except (TypeError, ValueError):
                pass

    raw_role = int(role_level or 0)

    
    if raw_role > GLOBAL_ROLE_OFFSET:
        return _effective_standard_role_level(raw_role) >= 15

    
    
    return 15 <= raw_role < MODIFIED_ROLE_OFFSET


def get_daily_transfer_limit(balance_data: dict, role_level: int = 0) -> Optional[int]:
    if has_unlimited_transfer_limit(role_level, balance_data):
        return None
    return 100_000_000_000 if has_active_vip(balance_data) else 1_000_000_000


def get_today_transfer_stats(balance_data: dict, role_level: int = 0):
    today = msk_now().date().isoformat()
    raw_stats = balance_data.get("transfers_today")
    if not isinstance(raw_stats, dict):
        raw_stats = {}
    spent_today = int(raw_stats.get(today, 0) or 0)
    balance_data["transfers_today"] = {today: spent_today}
    daily_limit = get_daily_transfer_limit(balance_data, role_level)
    remaining_today = None if daily_limit is None else max(0, daily_limit - spent_today)
    return today, spent_today, daily_limit, remaining_today


def parse_giveaway_duration(raw_value: str) -> int:
    value = str(raw_value).strip().lower()
    match = re.fullmatch(r"(\d+)([smhd]?)", value)
    if not match:
        raise ValueError("invalid duration")
    amount = int(match.group(1))
    suffix = match.group(2) or "m"
    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    seconds = amount * multipliers[suffix]
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds


TECH_ROLE_NAMES = {
    1: "Младший Технический Специалист",
    2: "Технический Специалист",
    3: "Старший Технический Специалист",
    4: "Куратор Технических Специалистов",
    5: "Заместитель Главного Технического Специалиста",
    6: "Главный Технический Специалист",
}

GLOBAL_LEVEL_TECH_CURATOR = 9
GLOBAL_TECH_CURATOR_ROLE_LEVEL = 12
GLOBAL_TECH_CURATOR_NAME = "Куратор Технических специалистов"
GLOBAL_LEVEL_QUALITY_CONTROL = 10
GLOBAL_QUALITY_CONTROL_ROLE_LEVEL = 16
GLOBAL_QUALITY_CONTROL_NAME = "Контроль качества"
GLOBAL_ROLE_LEVELS = {
    1: 8,   
    2: 10,  
    3: 13,  
    4: 11,  
    5: 14,  
    6: 15,  
    7: 17,  
    8: 9,   
    GLOBAL_LEVEL_TECH_CURATOR: GLOBAL_TECH_CURATOR_ROLE_LEVEL,
    GLOBAL_LEVEL_QUALITY_CONTROL: GLOBAL_QUALITY_CONTROL_ROLE_LEVEL,
}
GLOBAL_LEVEL_NAMES = {
    1: "Медиа Партнер",
    2: "Заместитель директора",
    3: "Заместитель Главного Тех. Специалиста",
    4: "Осн. заместитель директора",
    5: "Главный Тех. Специалист",
    6: "Директор бота",
    7: "Разработчик бота",
    8: "Технический специалист",
    GLOBAL_LEVEL_TECH_CURATOR: GLOBAL_TECH_CURATOR_NAME,
    GLOBAL_LEVEL_QUALITY_CONTROL: GLOBAL_QUALITY_CONTROL_NAME,
}
GLOBAL_ROLE_HIERARCHY_RANKS = {
    6: 15.0,  
    GLOBAL_LEVEL_QUALITY_CONTROL: 16.0,
    7: 17.0,  
}


_CURRENT_LOG_CHAT_ID: ContextVar[int | None] = ContextVar("current_log_chat_id", default=None)

GLOBAL_ROLE_OFFSET = 300
MODIFIED_ROLE_OFFSET = 100
MODIFIED_OWNER_ROLE = 200
MODIFIED_ROLE_COMMAND_THRESHOLDS = {
    0: 0,
    1: 1,
    2: 15,
    3: 30,
    4: 50,
    5: 70,
    6: 90,
    7: 100,
}


def _is_encoded_global_role(role: int) -> bool:
    return int(role or 0) > GLOBAL_ROLE_OFFSET


def _is_encoded_modified_role(role: int) -> bool:
    role = int(role or 0)
    return role == MODIFIED_OWNER_ROLE or MODIFIED_ROLE_OFFSET <= role < GLOBAL_ROLE_OFFSET


def _global_standard_role_level(role: int) -> int:
    global_level = int(role or 0) - GLOBAL_ROLE_OFFSET
    if global_level in GLOBAL_ROLE_LEVELS:
        return GLOBAL_ROLE_LEVELS.get(global_level, 0)
    return max(0, global_level)


def _modified_real_role_level(role: int) -> int:
    role = int(role or 0)
    if role == MODIFIED_OWNER_ROLE:
        return MODIFIED_ROLE_COMMAND_THRESHOLDS[7]
    if MODIFIED_ROLE_OFFSET <= role < GLOBAL_ROLE_OFFSET:
        return role - MODIFIED_ROLE_OFFSET
    return role


def _modified_standard_role_level(modified_level: int) -> int:
    standard_level = 0
    for level, threshold in MODIFIED_ROLE_COMMAND_THRESHOLDS.items():
        if int(modified_level or 0) >= threshold:
            standard_level = level
    return standard_level


def _effective_standard_role_level(role: int) -> int:
    role = int(role or 0)
    if _is_encoded_global_role(role):
        return _global_standard_role_level(role)
    if _is_encoded_modified_role(role):
        return _modified_standard_role_level(_modified_real_role_level(role))
    return role if 0 <= role <= 7 else 0


def _role_can_satisfy_standard_min(role: int, min_level: int) -> bool:
    min_level = int(min_level or 0)
    if min_level <= 0:
        return True
    if min_level >= 8 and not _is_encoded_global_role(role):
        return False
    return _effective_standard_role_level(role) >= min_level


def _role_hierarchy_value(role: int) -> float:
    role = int(role or 0)
    if _is_encoded_global_role(role):
        global_level = role - GLOBAL_ROLE_OFFSET
        rank = GLOBAL_ROLE_HIERARCHY_RANKS.get(global_level, _global_standard_role_level(role))
        return 1000 + rank
    if _is_encoded_modified_role(role):
        return 100 + _modified_real_role_level(role)
    return role if 0 <= role <= 7 else 0


ADMIN_NORM_ROLE_NAMES = {
    9: "Технический специалист",
    10: "Заместитель директора",
    11: "Основной заместитель директора",
    12: "Куратор техспециалистов",
    13: "Зам. Главного Тех. Специалиста",
    14: "Главный Тех. Специалист",
    15: "Директор бота",
    16: GLOBAL_QUALITY_CONTROL_NAME,
    17: "Разработчик бота",
}


def _admin_norm_kind(role_level: int) -> str:
    role_level = int(role_level or 0)
    if role_level == 9:
        return "tech_submit"
    if role_level in {12, 13, 14}:
        return "tech_review"
    if role_level == 16:
        return "messages_only"
    return "admin_actions"


def _empty_admin_stat_day(stat_date: str) -> dict:
    return {
        "stat_date": stat_date,
        "messages_count": 0,
        "gbanpl_count": 0,
        "gban_count": 0,
        "epoints_earned": 0,
        "norm_status": "not_completed",
        "forms_submitted": 0,
        "forms_accepted": 0,
        "resets_done": 0,
    }


def _admin_stat_int(data: dict, key: str) -> int:
    return int(data.get(key, 0) or 0)


async def _load_admin_stat_day(user_id: int, stat_date: str) -> dict:
    snapshot = _empty_admin_stat_day(stat_date)
    stat = await get_admin_stat(user_id, stat_date)
    if stat:
        snapshot.update(stat)
    norm_info = await get_admin_norm(user_id, stat_date)
    if norm_info:
        snapshot["forms_submitted"] = _admin_stat_int(norm_info, "forms_submitted")
        snapshot["forms_accepted"] = _admin_stat_int(norm_info, "forms_accepted")
        snapshot["resets_done"] = _admin_stat_int(norm_info, "resets_done")
        snapshot["norm_status"] = norm_info.get("norm_status") or snapshot["norm_status"]
    return snapshot


async def _load_admin_stats_days(user_id: int, days: int = 7) -> list[dict]:
    today = msk_now().date()
    result = []
    for i in range(days):
        day_str = (today - timedelta(days=i)).isoformat()
        result.append(await _load_admin_stat_day(user_id, day_str))
    return result


async def _merge_admin_norm_totals(
    user_id: int,
    stats_data: dict,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    data = dict(stats_data or {})
    norm_totals = await get_admin_norm_totals(user_id, start_date, end_date)
    data.update(norm_totals)
    for key in (
        "messages_count",
        "gbanpl_count",
        "gban_count",
        "epoints_earned",
        "forms_submitted",
        "forms_accepted",
        "resets_done",
    ):
        data[key] = _admin_stat_int(data, key)
    return data


def _admin_stats_title(role_level: int) -> str:
    kind = _admin_norm_kind(role_level)
    if kind == "tech_submit":
        return "Подробная статистика технического специалиста"
    if kind == "tech_review":
        return "Статистика тех. состава"
    if kind == "messages_only":
        return "Статистика контроля качества"
    return "Статистика администратора"


def _format_admin_stats_day_line(stat: dict, role_level: int) -> str:
    d = stat.get("stat_date", "")
    ep = format_number(_admin_stat_int(stat, "epoints_earned"))
    kind = _admin_norm_kind(role_level)
    if kind == "tech_submit":
        return (
            f"📅 {d}\n"
            f"  Сообщений: {_admin_stat_int(stat, 'messages_count')} | "
            f"Отправлено форм: {_admin_stat_int(stat, 'forms_submitted')} | Epoints: {ep}\n\n"
        )
    if kind == "tech_review":
        return (
            f"📅 {d}\n"
            f"  Принято форм: {_admin_stat_int(stat, 'forms_accepted')} | "
            f"Обнулений: {_admin_stat_int(stat, 'resets_done')} | Epoints: {ep}\n\n"
        )
    if kind == "messages_only":
        return f"📅 {d}\n  Сообщений: {_admin_stat_int(stat, 'messages_count')} | Epoints: {ep}\n\n"
    return (
        f"📅 {d}\n"
        f"  Сообщений: {_admin_stat_int(stat, 'messages_count')} | "
        f"/gbanpl: {_admin_stat_int(stat, 'gbanpl_count')} | "
        f"/gban: {_admin_stat_int(stat, 'gban_count')} | Epoints: {ep}\n\n"
    )


def _format_admin_stats_totals(data: dict, role_level: int, epoints_label: str) -> str:
    ep = format_number(_admin_stat_int(data, "epoints_earned"))
    kind = _admin_norm_kind(role_level)
    if kind == "tech_submit":
        return (
            f"Сообщений: {_admin_stat_int(data, 'messages_count')}\n"
            f"Отправлено форм: {_admin_stat_int(data, 'forms_submitted')}\n"
            f"{epoints_label}: {ep}"
        )
    if kind == "tech_review":
        return (
            f"Принято форм: {_admin_stat_int(data, 'forms_accepted')}\n"
            f"Обнулений/восстановлений: {_admin_stat_int(data, 'resets_done')}\n"
            f"{epoints_label}: {ep}"
        )
    if kind == "messages_only":
        return f"Сообщений: {_admin_stat_int(data, 'messages_count')}\n{epoints_label}: {ep}"
    return (
        f"Сообщений: {_admin_stat_int(data, 'messages_count')}\n"
        f"/gbanpl: {_admin_stat_int(data, 'gbanpl_count')}\n"
        f"/gban: {_admin_stat_int(data, 'gban_count')}\n"
        f"{epoints_label}: {ep}"
    )


def _build_admin_stats_detail_text(
    target_user_id: int,
    target_name: str,
    total_ep: int,
    date_display: str,
    stat: dict,
    role_level: int,
    status_text: str,
    epoints_reward: int,
) -> str:
    metrics = _format_admin_stats_totals(stat, role_level, "Получено Epoints за день")
    return (
        f"{_admin_stats_title(role_level)} - [id{target_user_id}|{target_name}]\n\n"
        f"Epoints - {format_number(total_ep)} баллов\n"
        f"Дата: {date_display}\n"
        f"{metrics}\n"
        f"Статус нормы: {status_text}\n"
        f"Награда за норму на текущем этапе: {format_number(epoints_reward)}"
    )


async def _build_admin_norm_progress_text(user_id: int, role_level: int, stat_date: str) -> str:
    stat = await _load_admin_stat_day(user_id, stat_date)
    kind = _admin_norm_kind(role_level)
    if kind == "tech_submit":
        return (
            "Текущий прогресс:\n"
            f"  • Отправлено форм: {_admin_stat_int(stat, 'forms_submitted')}\n"
            f"  • Сообщений: {_admin_stat_int(stat, 'messages_count')}"
        )
    if kind == "tech_review":
        return (
            "Текущий прогресс:\n"
            f"  • Принято форм: {_admin_stat_int(stat, 'forms_accepted')}\n"
            f"  • Обнулений/восстановлений: {_admin_stat_int(stat, 'resets_done')}"
        )
    if kind == "messages_only":
        return (
            "Текущий прогресс:\n"
            f"  • Сообщений: {_admin_stat_int(stat, 'messages_count')}"
        )
    return (
        "Текущий прогресс:\n"
        f"  • Сообщений: {_admin_stat_int(stat, 'messages_count')}\n"
        f"  • /gbanpl: {_admin_stat_int(stat, 'gbanpl_count')}\n"
        f"  • /gban: {_admin_stat_int(stat, 'gban_count')}"
    )


async def _record_admin_message_activity(user_id: int, chat_id: int) -> None:
    if int(user_id or 0) <= 0:
        return
    role_raw = await get_role(user_id, chat_id)
    if not _role_can_satisfy_standard_min(role_raw, 9):
        return
    role_level = _effective_standard_role_level(role_raw)
    today_str = msk_now().strftime("%Y-%m-%d")
    await log_admin_action(user_id, "message", today_str)
    await calculate_and_set_norm(user_id, role_level, today_str)


async def _recalculate_admin_norm_for_user(user_id: int, chat_id: int) -> None:
    if int(user_id or 0) <= 0:
        return
    role_raw = await get_role(user_id, chat_id)
    if not _role_can_satisfy_standard_min(role_raw, 9):
        return
    await calculate_and_set_norm(
        user_id,
        _effective_standard_role_level(role_raw),
        msk_now().strftime("%Y-%m-%d"),
    )


async def _custom_role_level_exists(chat_id: int, level: int) -> bool:
    try:
        fetch = await db_execute(
            f"SELECT 1 FROM custom_roles_{chat_id} WHERE level = ?",
            (int(level),), fetch='one'
        )
        return bool(fetch)
    except Exception:
        return False


async def _standard_role_level_for_target_chat(target_chat_id: int, standard_level: int) -> Optional[int]:
    standard_level = int(standard_level or 0)
    if standard_level < 1 or standard_level > 6:
        return None
    if await _is_modified_chat(target_chat_id):
        target_level = MODIFIED_ROLE_COMMAND_THRESHOLDS.get(standard_level)
        if not target_level or target_level >= MODIFIED_ROLE_COMMAND_THRESHOLDS[7]:
            return None
        return target_level if await _custom_role_level_exists(target_chat_id, target_level) else None
    return standard_level


async def _grant_standard_role_to_user(user_id: int, chat_id: int, standard_level: int) -> bool:
    target_level = await _standard_role_level_for_target_chat(chat_id, standard_level)
    if target_level is None:
        return False
    await roleG(user_id, chat_id, target_level)
    return True


async def _smsrole_level_for_target_chat(source_chat_id: int, target_chat_id: int, level: int) -> Optional[int]:
    level = int(level or 0)
    if await _is_modified_chat(source_chat_id):
        if await _is_modified_chat(target_chat_id):
            return level if await _custom_role_level_exists(target_chat_id, level) else None
        standard_level = _modified_standard_role_level(level)
        return standard_level if 1 <= standard_level <= 6 else None
    return await _standard_role_level_for_target_chat(target_chat_id, level)


def _ensure_modified_role_tables(chat_id: int) -> None:
    chat_id = int(chat_id)
    sql.execute("INSERT OR IGNORE INTO modified_chats (chat_id) VALUES (?)", (chat_id,))
    sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (0, 'Пользователь')")
    sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
    sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")


def _get_modified_role_snapshot(chat_id: int) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    chat_id = int(chat_id)
    _ensure_modified_role_tables(chat_id)

    sql.execute(
        f"SELECT level, name FROM custom_roles_{chat_id} WHERE level BETWEEN 1 AND 100 ORDER BY level"
    )
    role_map: dict[int, str] = {}
    for level, name in sql.fetchall():
        level = int(level or 0)
        name = str(name or "").strip()
        if 1 <= level <= 100 and name:
            role_map[level] = name
    role_map[100] = role_map.get(100) or "Владелец беседы"

    custom_levels = {level for level in role_map if 1 <= level <= 99}
    sql.execute(
        f"SELECT user_id, level FROM permissions_{chat_id} WHERE level BETWEEN 1 AND 99 ORDER BY rowid"
    )
    permission_map: dict[int, int] = {}
    for target_user_id, level in sql.fetchall():
        level = int(level or 0)
        if level not in custom_levels:
            continue
        permission_map[int(target_user_id)] = level

    roles = sorted(role_map.items(), key=lambda item: item[0])
    permissions = sorted(permission_map.items(), key=lambda item: item[0])
    return roles, permissions


def _get_local_role_snapshot_as_modified(chat_id: int) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    chat_id = int(chat_id)
    standard_role_names = {
        1: "Модератор",
        2: "Старший модератор",
        3: "Администратор",
        4: "Старший администратор",
        5: "Зам. спец администратора",
        6: "Спец. администратор",
    }
    roles = [
        (MODIFIED_ROLE_COMMAND_THRESHOLDS[level], name)
        for level, name in standard_role_names.items()
    ]
    roles.append((100, "Владелец беседы"))

    sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
    sql.execute(
        f"SELECT user_id, level FROM permissions_{chat_id} WHERE level BETWEEN 1 AND 6 ORDER BY rowid"
    )
    permission_map: dict[int, int] = {}
    for target_user_id, level in sql.fetchall():
        modified_level = MODIFIED_ROLE_COMMAND_THRESHOLDS.get(int(level or 0))
        if modified_level and 1 <= modified_level <= 99:
            permission_map[int(target_user_id)] = modified_level

    permissions = sorted(permission_map.items(), key=lambda item: item[0])
    return roles, permissions


def _apply_modified_role_snapshot(
    chat_id: int,
    roles: list[tuple[int, str]],
    permissions: list[tuple[int, int]]
) -> None:
    chat_id = int(chat_id)
    _ensure_modified_role_tables(chat_id)

    sql.execute(f"DELETE FROM custom_roles_{chat_id}")
    sql.executemany(
        f"INSERT OR REPLACE INTO custom_roles_{chat_id} (level, name) VALUES (?, ?)",
        roles
    )

    valid_levels = {int(level) for level, _name in roles if 1 <= int(level) <= 99}
    clean_permissions = [
        (int(target_user_id), int(level))
        for target_user_id, level in permissions
        if int(level) in valid_levels
    ]

    sql.execute(f"DELETE FROM permissions_{chat_id}")
    if clean_permissions:
        sql.executemany(
            f"INSERT INTO permissions_{chat_id} (user_id, level) VALUES (?, ?)",
            clean_permissions
        )


async def is_user_subscribed_to_bot_group(user_id: int) -> bool:
    try:
        resp = await bot.api.groups.is_member(group_id=groupid, user_id=user_id)
        member = getattr(resp, "member", None)
        if member is None:
            member = bool(resp)
        return bool(member)
    except Exception as e:
        print(f"[giveaway] is_member error for {user_id}: {e}")
        return False


async def has_required_bot_subscription(user_id: int) -> bool:
    
    
    return True


def get_subscription_required_text() -> str:
    return f"❗ Сначала подпишитесь на сообщество: {BOT_GROUP_URL}"


def is_bot_command_invocation(first_token: str) -> bool:
    token = str(first_token or "").strip()
    if len(token) < 2 or token[0] not in BOT_COMMAND_PREFIXES:
        return False
    command_body = token[1:].strip()
    return bool(command_body) and command_body[0].isalpha()


async def has_subscription_ever_claimed(user_id: int) -> bool:
    await ensure_runtime_schema()
    async with aiosqlite.connect("database.db", timeout=30) as db:
        cur = await db.execute("SELECT 1 FROM subscription_claims WHERE user_id = ? LIMIT 1", (int(user_id),))
        return await cur.fetchone() is not None


async def mark_subscription_ever_claimed(user_id: int) -> None:
    await ensure_runtime_schema()
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO subscription_claims (user_id, claimed_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (int(user_id), datetime.now().isoformat()),
            )
            await db.commit()
        finally:
            await db.close()


async def migrate_subscription_claims_to_db() -> int:
    await ensure_runtime_schema()
    old_subs = load_json_file(SUBS_FILE)
    migrated = 0
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            for user_key, sub_info in old_subs.items():
                if not isinstance(sub_info, dict) or not (sub_info.get("ever_claimed") or sub_info.get("claimed")):
                    continue
                try:
                    user_id_int = int(user_key)
                except Exception:
                    continue
                await db.execute(
                    """
                    INSERT INTO subscription_claims (user_id, claimed_at)
                    VALUES (?, ?)
                    ON CONFLICT(user_id) DO NOTHING
                    """,
                    (user_id_int, str(sub_info.get("time") or sub_info.get("revoked_at") or datetime.now().isoformat())),
                )
                migrated += 1
            await db.commit()
        finally:
            await db.close()
    return migrated


async def _configure_async_db(db):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")



def _global_role_user_ids() -> set[str]:
    try:
        with sqlite3.connect("database.db") as db:
            rows = db.execute("SELECT user_id FROM global_managers WHERE level > 0").fetchall()
            return {str(int(row[0])) for row in rows}
    except Exception:
        return set()


def _parse_ep_quantity_text(raw_text: str) -> Optional[int]:
    cleaned = re.sub(r"[\s._,]", "", str(raw_text or ""))
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if value > 0 else None

def _available_branch_numbers(existing_branch_numbers: set[int], quantity: int) -> list[int]:
    result = []
    for branch_no in range(1, MAX_BRANCHES_PER_BUSINESS + 1):
        if branch_no not in existing_branch_numbers:
            result.append(branch_no)
            if len(result) >= quantity:
                return result
    return result

def _fit_vk_button_label(label: str, max_len: int = 40) -> str:
    label = str(label or "").strip()
    if len(label) <= max_len:
        return label
    return label[: max_len - 1].rstrip() + "…"


def format_vk_link(user_id: int, name: str) -> str:
    safe_name = str(name).replace("]", ")").replace("\n", " ").replace("\r", " ")
    if int(user_id) < 0:
        return f"[club{abs(int(user_id))}|{safe_name}]"
    return f"@id{int(user_id)} ({safe_name})"


def format_vk_profile_link(user_id: int, name: str) -> str:
    safe_name = str(name).replace("]", ")").replace("\n", " ").replace("\r", " ")
    if int(user_id) < 0:
        return f"[club{abs(int(user_id))}|{safe_name}]"
    return f"[id{int(user_id)}|{safe_name}]"


async def get_vk_entity_name(entity_id: int) -> str:
    entity_id = int(entity_id)
    now_ts = time.time()
    cached = vk_entity_name_cache.get(entity_id)
    if cached and cached[1] > now_ts:
        return cached[0]
    name = "Не удалось получить имя"
    try:
        if entity_id < 0:
            group_id = abs(entity_id)
            group_info = await bot.api.groups.get_by_id(group_ids=str(group_id))
            if isinstance(group_info, list) and group_info:
                name = str(getattr(group_info[0], "name", "") or name)
                vk_entity_name_cache[entity_id] = (name, time.time() + VK_ENTITY_NAME_CACHE_TTL_SECONDS)
                return name
            if hasattr(group_info, "groups") and group_info.groups:
                name = str(getattr(group_info.groups[0], "name", "") or name)
                vk_entity_name_cache[entity_id] = (name, time.time() + VK_ENTITY_NAME_CACHE_TTL_SECONDS)
                return name
            if hasattr(group_info, "name"):
                name = str(getattr(group_info, "name", "") or name)
                vk_entity_name_cache[entity_id] = (name, time.time() + VK_ENTITY_NAME_CACHE_TTL_SECONDS)
                return name
            vk_entity_name_cache[entity_id] = (name, time.time() + 30)
            return name
        user_info = await bot.api.users.get(user_ids=entity_id)
        if user_info:
            name = f"{user_info[0].first_name} {user_info[0].last_name}"
    except Exception:
        pass
    vk_entity_name_cache[entity_id] = (name, time.time() + VK_ENTITY_NAME_CACHE_TTL_SECONDS)
    return name


async def get_chat_display_info(
    chat_id: int,
    *,
    refresh_live: bool = True,
    fetch_invite_link: bool = True,
) -> tuple[str, str | None]:
    chat_id = int(chat_id)
    peer_id = 2000000000 + chat_id
    cached_title = None
    cached_link = None

    try:
        sql.execute("SELECT chat_title, invite_link FROM chats WHERE chat_id = ?", (chat_id,))
        row = sql.fetchone()
        if row:
            cached_title = row[0]
            cached_link = row[1]
    except Exception:
        pass

    if not cached_title:
        try:
            sql.execute("SELECT chat_title FROM server_links WHERE chat_id = ?", (chat_id,))
            row = sql.fetchone()
            if row and row[0]:
                cached_title = row[0]
        except Exception:
            pass

    title = cached_title or f"Беседа "
    link = cached_link if isinstance(cached_link, str) and cached_link.startswith("http") else None
    updated = False

    if not refresh_live:
        return title, link

    try:
        response = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
        if response.items:
            live_title = getattr(response.items[0].chat_settings, "title", None)
            if live_title:
                title = live_title
                updated = True
    except Exception as error:
        print(f"[get_chat_display_info] Не удалось получить название чата {chat_id}: {error}")

    if fetch_invite_link:
        try:
            link_response = await bot.api.messages.get_invite_link(peer_id=peer_id, reset=0)
            live_link = getattr(link_response, "link", None)
            if isinstance(live_link, str) and live_link.startswith("http"):
                link = live_link
                updated = True
        except Exception as error:
            error_text = str(error).lower()
            if "invite link" not in error_text and "can't see" not in error_text:
                print(f"[get_chat_display_info] Не удалось получить ссылку чата {chat_id}: {error}")

    if updated:
        try:
            sql.execute(
                "UPDATE chats SET chat_title = ?, invite_link = ? WHERE chat_id = ?",
                (title, link, chat_id),
            )
            sql.execute(
                "UPDATE server_links SET chat_title = ? WHERE chat_id = ?",
                (title, chat_id),
            )
            database.commit()
        except Exception as error:
            print(f"[get_chat_display_info] Не удалось сохранить данные чата {chat_id}: {error}")

    return title, link


def format_chat_link_text(link: str | None) -> str:
    if isinstance(link, str) and link.startswith("http"):
        return f"[{link}|Ссылка на чат]"
    return "Ссылка на чат недоступна"


async def get_chat_availability(chat_id: int) -> tuple[bool, str]:
    chat_id = int(chat_id)
    peer_id = 2000000000 + chat_id
    try:
        response = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
        if not getattr(response, "items", None):
            return False, "чат недоступен или удалён"
    except Exception as error:
        return False, f"чат недоступен: {str(error)[:80]}"

    try:
        members = await bot.api.messages.get_conversation_members(peer_id=peer_id)
        bot_member_id = -abs(int(groupid))
        for member in getattr(members, "items", None) or []:
            member_id = int(getattr(member, "member_id", 0) or 0)
            if member_id != bot_member_id:
                continue
            if bool(getattr(member, "is_admin", False) or getattr(member, "is_owner", False)):
                return True, "ok"
            return False, "у бота нет прав администратора"
        return False, "бот не найден среди участников"
    except Exception as error:
        return False, f"не удалось проверить права бота: {str(error)[:80]}"


async def get_active_registered_chats(
    *,
    force_refresh: bool = False,
) -> tuple[list[tuple[int, int]], list[tuple[int, int, str]]]:
    now_ts = time.time()
    if not force_refresh and float(registered_chats_cache.get("expires_at", 0.0) or 0.0) > now_ts:
        return (
            list(registered_chats_cache.get("active_rows", []) or []),
            list(registered_chats_cache.get("inactive_rows", []) or []),
        )

    async with registered_chats_cache_lock:
        now_ts = time.time()
        if not force_refresh and float(registered_chats_cache.get("expires_at", 0.0) or 0.0) > now_ts:
            return (
                list(registered_chats_cache.get("active_rows", []) or []),
                list(registered_chats_cache.get("inactive_rows", []) or []),
            )

        active_rows, inactive_rows = await _load_active_registered_chats_uncached()
        registered_chats_cache["active_rows"] = list(active_rows)
        registered_chats_cache["inactive_rows"] = list(inactive_rows)
        registered_chats_cache["expires_at"] = time.time() + REGISTERED_CHATS_CACHE_TTL_SECONDS
        return list(active_rows), list(inactive_rows)


async def _load_active_registered_chats_uncached() -> tuple[list[tuple[int, int]], list[tuple[int, int, str]]]:
    sql.execute("SELECT chat_id, owner_id FROM chats ORDER BY chat_id ASC")
    rows = [(int(row[0]), int(row[1] or 0)) for row in sql.fetchall()]
    check_semaphore = asyncio.Semaphore(REGISTERED_CHAT_CHECK_CONCURRENCY)

    async def check_row(row: tuple[int, int]) -> tuple[bool, int, int, str]:
        chat_id_row, owner_id = row
        try:
            async with check_semaphore:
                is_active, reason = await asyncio.wait_for(
                    get_chat_availability(chat_id_row),
                    timeout=REGISTERED_CHAT_CHECK_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            is_active, reason = False, "таймаут проверки беседы"
        except Exception as error:
            is_active, reason = False, f"ошибка проверки: {str(error)[:80]}"
        return is_active, chat_id_row, owner_id, reason

    checked_rows = await asyncio.gather(*(check_row(row) for row in rows))
    active_rows: list[tuple[int, int]] = []
    inactive_rows: list[tuple[int, int, str]] = []
    for is_active, chat_id_row, owner_id, reason in checked_rows:
        if is_active:
            active_rows.append((chat_id_row, owner_id))
        else:
            inactive_rows.append((chat_id_row, owner_id, reason))

    return active_rows, inactive_rows


async def build_chats_registry_page(rows: list[tuple[int, int]], page: int, per_page: int = 20) -> tuple[str, int]:
    total = len(rows)
    max_page = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), max_page))
    start = (page - 1) * per_page
    end = start + per_page
    selected = rows[start:end]
    line_semaphore = asyncio.Semaphore(8)

    async def build_selected_line(idx: int, chat_id_row: int, owner_id: int) -> str:
        async with line_semaphore:
            return await build_chat_registry_line(idx, chat_id_row, owner_id)

    formatted = await asyncio.gather(
        *(
            build_selected_line(idx, chat_id_row, owner_id)
            for idx, (chat_id_row, owner_id) in enumerate(selected, start=start + 1)
        )
    )
    return "\n".join(formatted) or "Беседы отсутствуют!", max_page


async def build_chat_registry_line(index: int, chat_id: int, owner_id: int) -> str:
    chat_title, chat_link = await get_chat_display_info(chat_id, refresh_live=False, fetch_invite_link=False)
    owner_name = await get_vk_entity_name(owner_id)
    return f"{index}) {chat_id} | {chat_title} | {format_vk_link(owner_id, owner_name)} | {format_chat_link_text(chat_link)}"

_ANSWERED_CALLBACK_EVENTS: Dict[str, float] = {}
_CALLBACK_EVENT_CACHE_TTL_SECONDS = 120.0


async def answer_callback_event(event_message, text: str = "Готово") -> bool:
    """Безопасно подтверждает callback VK не более одного раза.

    VK принимает event_id только короткое время и только один раз. Просроченный
    event_id не должен ломать обработчик или засорять лог ошибками.
    """
    event_object = getattr(event_message, "object", None)
    event_id = str(getattr(event_object, "event_id", "") or "").strip()
    peer_id = getattr(event_object, "peer_id", None)
    user_id = getattr(event_object, "user_id", None)
    if not event_id or peer_id is None or user_id is None:
        return False

    now_ts = time.monotonic()
    expired_before = now_ts - _CALLBACK_EVENT_CACHE_TTL_SECONDS
    for cached_event_id, answered_at in list(_ANSWERED_CALLBACK_EVENTS.items()):
        if answered_at < expired_before:
            _ANSWERED_CALLBACK_EVENTS.pop(cached_event_id, None)

    if event_id in _ANSWERED_CALLBACK_EVENTS:
        return False

    
    _ANSWERED_CALLBACK_EVENTS[event_id] = now_ts
    try:
        await bot.api.messages.send_message_event_answer(
            event_id=event_id,
            peer_id=int(peer_id),
            user_id=int(user_id),
            event_data=json.dumps(
                {"type": "show_snackbar", "text": str(text)[:90]},
                ensure_ascii=False,
            ),
        )
        return True
    except Exception as exc:
        error_text = str(exc).lower()
        
        
        if "invalid event_id" not in error_text and "missing or invalid" not in error_text:
            print(f"[CALLBACK_ANSWER] Ошибка: {exc}")
        return False


def _callback_owner_allowed(payload: dict, user_id: int) -> bool:
    owner_id = int(payload.get("owner_id", 0) or 0)
    return owner_id == 0 or owner_id == user_id


def build_gzov_disable_keyboard(chat_id: int) -> Keyboard:
    kb = Keyboard(inline=True)
    kb.add(
        Callback("Отключить рассылку", {"command": "gzov_disable", "chatId": int(chat_id)}),
        color=KeyboardButtonColor.NEGATIVE
    )
    return kb


async def upload_message_photo(peer_id: int, file_path: str) -> Optional[str]:
    if file_path and not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    if not file_path or not os.path.exists(file_path):
        return None
    file_path = os.path.abspath(file_path)
    cache_allowed = f"{os.sep}generated{os.sep}" not in file_path.lower()
    if cache_allowed:
        cached_attachment = uploaded_message_photo_cache.get(file_path)
        if cached_attachment:
            return cached_attachment

    lock = uploaded_message_photo_locks.setdefault(file_path, asyncio.Lock())
    try:
        async with lock:
            if cache_allowed:
                cached_attachment = uploaded_message_photo_cache.get(file_path)
                if cached_attachment:
                    return cached_attachment

            for attempt in range(3):
                try:
                    upload = await bot.api.photos.get_messages_upload_server(peer_id=peer_id)
                    upload_url = upload.upload_url if hasattr(upload, "upload_url") else upload["upload_url"]
                    data = aiohttp.FormData()
                    with open(file_path, "rb") as photo_file:
                        content_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
                        data.add_field("photo", photo_file, filename=os.path.basename(file_path), content_type=content_type)
                        async with aiohttp.ClientSession() as session:
                            async with session.post(upload_url, data=data) as resp:
                                uploaded = await resp.json()
                    saved = await bot.api.photos.save_messages_photo(
                        **uploaded if isinstance(uploaded, dict) else uploaded.model_dump()
                    )
                    if saved:
                        
                        if isinstance(saved, list) and len(saved) > 0:
                            photo = saved[0]
                            
                            owner_id = getattr(photo, "owner_id", None) or (photo.owner_id if hasattr(photo, "owner_id") else None)
                            photo_id = getattr(photo, "id", None) or (photo.id if hasattr(photo, "id") else None)
                            if owner_id and photo_id:
                                attachment = f"photo{owner_id}_{photo_id}"
                                if cache_allowed:
                                    uploaded_message_photo_cache[file_path] = attachment
                                return attachment
                except Exception as upload_error:
                    if attempt == 2:
                        raise upload_error
                    await asyncio.sleep(0.35 * (attempt + 1))
    except Exception as e:
        log.exception("Ошибка загрузки фото в сообщение: %s", e)
    return None


def extract_user_id(message: Message):
    
    if message.reply_message:
        reply_from_id = getattr(message.reply_message, "from_id", None)
        if isinstance(reply_from_id, int) and reply_from_id > 0:
            return reply_from_id
    elif message.fwd_messages:
        forward_from_id = getattr(message.fwd_messages[0], "from_id", None)
        if isinstance(forward_from_id, int) and forward_from_id > 0:
            return forward_from_id

    text = message.text or ""
    m = re.search(r"\[id(\d+)\|", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:@id|id)(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"vk\.com/id(\d+)", text)
    if m:
        return int(m.group(1))
    return None


class Localization:
    def __init__(self, path: str):
        self.data = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.data = yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Localization file {path} not found!")

    def get(self, key: str, **kwargs) -> str:
        parts = key.split(".")
        value = self.data
        try:
            for part in parts:
                value = value[part]
        except (KeyError, TypeError):
            return f"No translation ({key})"  
        
        def repl(match):
            var_name = match.group(1)
            return str(kwargs.get(var_name, f"$({var_name})"))
        return re.sub(r"\$\((\w+)\)", repl, value)     


loc = Localization("localization.yml")

_ORIGINAL_MESSAGE_REPLY = Message.reply
_ORIGINAL_MESSAGE_ANSWER = Message.answer


async def _send_message_direct(peer_id: int, text: str = "", keyboard=None, disable_mentions=None, attachment=None):
    await bot.api.messages.send(
        peer_id=peer_id,
        random_id=0,
        message=text,
        keyboard=keyboard,
        disable_mentions=disable_mentions,
        attachment=attachment,
    )


def _as_positive_int(value) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _extract_sent_message_value(send_result, field_names: tuple[str, ...]) -> Optional[int]:
    if isinstance(send_result, int) and "id" in field_names:
        return _as_positive_int(send_result)
    if isinstance(send_result, list) and send_result:
        return _extract_sent_message_value(send_result[0], field_names)
    if isinstance(send_result, dict):
        for key in field_names:
            value = send_result.get(key)
            parsed = _as_positive_int(value)
            if parsed is not None:
                return parsed
    for attr_name in field_names:
        value = getattr(send_result, attr_name, None)
        parsed = _as_positive_int(value)
        if parsed is not None:
            return parsed
    return None


async def _resolve_sent_message_cmid(peer_id: int, send_result) -> Optional[int]:
    cmid = _extract_sent_message_value(send_result, ("conversation_message_id", "cmid"))
    if cmid is not None:
        return cmid

    message_id = _extract_sent_message_value(send_result, ("message_id", "id"))
    if message_id is None:
        return None

    try:
        response = await bot.api.messages.get_by_id(message_ids=[message_id], peer_id=peer_id)
        items = getattr(response, "items", None) or []
        if items:
            resolved_cmid = _extract_sent_message_value(items[0], ("conversation_message_id", "cmid"))
            if resolved_cmid is not None:
                return resolved_cmid
    except Exception as error:
        print(f"[restart] resolve cmid error: {error}")

    return None


async def _find_recent_message_cmid(peer_id: int, expected_text: str) -> Optional[int]:
    try:
        history = await bot.api.messages.get_history(peer_id=peer_id, count=20)
        for item in getattr(history, "items", None) or []:
            item_text = str(getattr(item, "text", "") or "")
            if item_text != expected_text and not item_text.startswith("🎲 Началась игра в кости!"):
                continue
            cmid = _extract_sent_message_value(item, ("conversation_message_id", "cmid"))
            if cmid is not None:
                return cmid
    except Exception as error:
        print(f"[restart] find recent cmid error: {error}")
    return None


RESTART_PROGRESS_STATE_FILE = "restart_progress_state.json"
RESTART_PROGRESS_ESTIMATE_FILE = "restart_progress_estimate.json"
RESTART_PROGRESS_WORKER_SCRIPT = os.path.join("utils", "restart_progress_worker.py")
RESTART_PROGRESS_DONE_TEXT = "\u0411\u043e\u0442 \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0449\u0435\u043d."
RESTART_PRE_EXEC_MAX_PERCENT = 94
RESTART_DEFAULT_ESTIMATE_SECONDS = 8.0
RESTART_MIN_ESTIMATE_SECONDS = 4.0
RESTART_MAX_ESTIMATE_SECONDS = 30.0


def _clamp_restart_seconds(value: float) -> float:
    return max(RESTART_MIN_ESTIMATE_SECONDS, min(RESTART_MAX_ESTIMATE_SECONDS, float(value)))


def _load_restart_progress_estimate() -> float:
    state = load_json_file(RESTART_PROGRESS_ESTIMATE_FILE)
    if not isinstance(state, dict):
        return RESTART_DEFAULT_ESTIMATE_SECONDS
    try:
        return _clamp_restart_seconds(float(state.get("estimate_seconds") or RESTART_DEFAULT_ESTIMATE_SECONDS))
    except Exception:
        return RESTART_DEFAULT_ESTIMATE_SECONDS


def _save_restart_progress_estimate(actual_seconds: float) -> None:
    try:
        actual_seconds = _clamp_restart_seconds(float(actual_seconds))
        previous = _load_restart_progress_estimate()
        estimate_seconds = _clamp_restart_seconds(previous * 0.65 + actual_seconds * 0.35)
        save_json_file(
            RESTART_PROGRESS_ESTIMATE_FILE,
            {
                "estimate_seconds": estimate_seconds,
                "last_duration_seconds": actual_seconds,
                "updated_at": time.time(),
            },
        )
    except Exception as error:
        print(f"[restart] save estimate error: {error}")


def _restart_elapsed_percent(started_at: float, estimate_seconds: float, max_percent: int = RESTART_PRE_EXEC_MAX_PERCENT) -> int:
    try:
        elapsed = max(0.0, time.time() - float(started_at))
        estimate_seconds = _clamp_restart_seconds(float(estimate_seconds))
        percent = round(max_percent * min(1.0, elapsed / estimate_seconds))
    except Exception:
        percent = 0
    return max(0, min(int(max_percent), int(percent)))


def _restart_progress_bar(percent: int, total_segments: int = 18) -> str:
    percent = max(0, min(100, int(percent)))
    filled = int(round(percent * total_segments / 100))
    filled = max(0, min(total_segments, filled))
    return "\u2588" * filled + "\u2591" * (total_segments - filled)


def _build_restart_progress_text(percent: int, frame_index: int = 0) -> str:
    percent = max(0, min(100, int(percent)))
    bar = _restart_progress_bar(percent)
    return f"\u041f\u0435\u0440\u0435\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0431\u043e\u0442\u0430\n[{bar}] {percent:3d}%"


def _save_pending_restart_progress(peer_id: int, cmid: int, started_at: Optional[float] = None, estimate_seconds: Optional[float] = None) -> dict:
    started_at = float(started_at or time.time())
    estimate_seconds = _clamp_restart_seconds(float(estimate_seconds or _load_restart_progress_estimate()))
    state = {
        "peer_id": int(peer_id),
        "cmid": int(cmid),
        "created_at": time.time(),
        "started_at": started_at,
        "estimate_seconds": estimate_seconds,
        "done": False,
    }
    save_json_file(RESTART_PROGRESS_STATE_FILE, state)
    return state


def _load_pending_restart_progress() -> dict:
    state = load_json_file(RESTART_PROGRESS_STATE_FILE)
    if not isinstance(state, dict):
        return {}
    try:
        peer_id = int(state.get("peer_id") or 0)
        cmid = int(state.get("cmid") or 0)
        created_at = float(state.get("created_at") or 0)
        started_at = float(state.get("started_at") or created_at)
        estimate_seconds = _clamp_restart_seconds(float(state.get("estimate_seconds") or _load_restart_progress_estimate()))
    except Exception:
        return {}
    if peer_id <= 0 or cmid <= 0 or time.time() - created_at > 600:
        return {}
    return {
        "peer_id": peer_id,
        "cmid": cmid,
        "created_at": created_at,
        "started_at": started_at,
        "estimate_seconds": estimate_seconds,
        "done": bool(state.get("done")),
    }


def _mark_pending_restart_progress_done() -> None:
    state = load_json_file(RESTART_PROGRESS_STATE_FILE)
    if not isinstance(state, dict):
        return
    state["done"] = True
    state["done_at"] = time.time()
    save_json_file(RESTART_PROGRESS_STATE_FILE, state)


def _clear_pending_restart_progress() -> None:
    try:
        if os.path.exists(RESTART_PROGRESS_STATE_FILE):
            os.remove(RESTART_PROGRESS_STATE_FILE)
    except Exception as error:
        print(f"[restart] clear state error: {error}")


def _start_restart_progress_worker(peer_id: int, cmid: int, started_at: float, estimate_seconds: float) -> bool:
    script_path = os.path.abspath(RESTART_PROGRESS_WORKER_SCRIPT)
    if not os.path.exists(script_path):
        print(f"[restart] worker script missing: {script_path}")
        return False
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        subprocess.Popen(
            [
                sys.executable,
                script_path,
                os.path.abspath(RESTART_PROGRESS_STATE_FILE),
                str(int(peer_id)),
                str(int(cmid)),
                str(float(started_at)),
                str(float(estimate_seconds)),
            ],
            cwd=os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return True
    except Exception as error:
        print(f"[restart] worker start error: {error}")
        return False


async def _finish_pending_restart_progress() -> None:
    state = _load_pending_restart_progress()
    if not state:
        return
    if state.get("done"):
        _clear_pending_restart_progress()
        return
    peer_id = int(state["peer_id"])
    cmid = int(state["cmid"])
    started_at = float(state["started_at"])
    actual_seconds = max(0.0, time.time() - started_at)
    _save_restart_progress_estimate(actual_seconds)
    try:
        _mark_pending_restart_progress_done()
        await asyncio.sleep(0.8)
        await _edit_bot_message(peer_id, cmid, _build_restart_progress_text(100), flood_retries=2)
        await asyncio.sleep(1.4)
        await _edit_bot_message(peer_id, cmid, RESTART_PROGRESS_DONE_TEXT, flood_retries=2)
    finally:
        _clear_pending_restart_progress()


def _is_vk_flood_error(error: Exception) -> bool:
    error_text = str(error).lower()
    return "flood control" in error_text or "too much messages" in error_text


async def _edit_bot_message(
    peer_id: int,
    sent_message_id: Optional[int],
    text: str,
    *,
    flood_retries: int = 1,
) -> bool:
    if sent_message_id is None:
        return False
    last_error = None
    for attempt in range(max(0, int(flood_retries)) + 1):
        try:
            await bot.api.messages.edit(
                peer_id=peer_id,
                conversation_message_id=sent_message_id,
                message=text,
                group_id=groupid,
            )
            return True
        except Exception as error:
            last_error = error
            if _is_vk_flood_error(error) and attempt < flood_retries:
                await asyncio.sleep(3.0 + attempt * 2.0)
                continue
            break
    print(f"[restart] edit message error: {last_error}")
    return False


async def safe_message_reply(self, message: str = "", keyboard=None, disable_mentions=None, attachment=None, **kwargs):
    text = message if message is not None else str(kwargs.pop("message", "") or "")
    try:
        return await _ORIGINAL_MESSAGE_REPLY(
            self,
            text,
            keyboard=keyboard,
            disable_mentions=disable_mentions,
            attachment=attachment,
            **kwargs,
        )
    except Exception as send_error:
        print(f"[SAFE REPLY FALLBACK] peer_id={getattr(self, 'peer_id', None)} error: {send_error}")
        await _send_message_direct(
            peer_id=self.peer_id,
            text=text,
            keyboard=keyboard,
            disable_mentions=disable_mentions,
            attachment=attachment,
        )


async def safe_message_answer(self, message: str = "", keyboard=None, disable_mentions=None, attachment=None, **kwargs):
    text = message if message is not None else str(kwargs.pop("message", "") or "")
    try:
        return await _ORIGINAL_MESSAGE_ANSWER(
            self,
            text,
            keyboard=keyboard,
            disable_mentions=disable_mentions,
            attachment=attachment,
            **kwargs,
        )
    except Exception as send_error:
        print(f"[SAFE ANSWER FALLBACK] peer_id={getattr(self, 'peer_id', None)} error: {send_error}")
        await _send_message_direct(
            peer_id=self.peer_id,
            text=text,
            keyboard=keyboard,
            disable_mentions=disable_mentions,
            attachment=attachment,
        )


Message.reply = safe_message_reply
Message.answer = safe_message_answer


async def replyLocalizedMessage(self, key: str, variables: dict = None, keyboard=None):
    text = loc.get(key, **(variables or {}))
    if text.startswith("No translation"):
        await self.reply(text, keyboard=keyboard)
        return
    await self.reply(text, keyboard=keyboard)

Message.replyLocalizedMessage = replyLocalizedMessage


def extract_user_id_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\[id(\d+)\|", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:@id|id)(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"vk(?:\.com|\.ru)/id(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{4,})\b", text)
    if m:
        return int(m.group(1))
    return None


def remove_user_reference_from_text(text: str, user_id: int) -> str:
    value = str(text or "")
    if not user_id or user_id <= 0:
        return value.strip()
    uid = int(user_id)
    patterns = [
        rf"\[[^\]]*\]\(\s*(?:https?://)?(?:m\.)?vk\.(?:com|ru)/id{uid}\b/?\s*\)",
        rf"\[id{uid}\|[^\]]*\]",
        rf"https?://(?:m\.)?vk\.(?:com|ru)/id{uid}\b/?",
        rf"(?:m\.)?vk\.(?:com|ru)/id{uid}\b/?",
        rf"@id{uid}\b",
        rf"(?<![\w@/])id{uid}\b",
    ]
    for pattern in patterns:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def extract_trailing_standalone_int(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"(?:^|\s)(\d+)\s*$", str(text).strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None
    
def _message_targets_own_bot(message: Message) -> bool:
    """Проверяет, пытается ли команда воздействовать на сообщество этого бота."""
    own_group_id = abs(int(groupid))
    own_from_id = -own_group_id

    reply_message = getattr(message, "reply_message", None)
    if reply_message is not None:
        try:
            if int(getattr(reply_message, "from_id", 0) or 0) == own_from_id:
                return True
        except (TypeError, ValueError):
            pass

    for forwarded in (getattr(message, "fwd_messages", None) or []):
        try:
            if int(getattr(forwarded, "from_id", 0) or 0) == own_from_id:
                return True
        except (TypeError, ValueError):
            continue

    text = str(getattr(message, "text", "") or "").lower()
    compact = re.sub(r"\s+", "", text)
    group_patterns = (
        f"[club{own_group_id}|",
        f"@club{own_group_id}",
        f"club{own_group_id}",
        f"vk.com/club{own_group_id}",
        f"vk.ru/club{own_group_id}",
        f"vk.me/club{own_group_id}",
        str(own_from_id),
    )
    if any(pattern in compact for pattern in group_patterns):
        return True

    
    try:
        screen_name = BOT_GROUP_URL.rstrip("/").rsplit("/", 1)[-1].strip().lower()
    except Exception:
        screen_name = ""
    if screen_name:
        screen_patterns = (
            f"@{screen_name}",
            f"vk.com/{screen_name}",
            f"vk.ru/{screen_name}",
            f"vk.me/{screen_name}",
        )
        if any(pattern in compact for pattern in screen_patterns):
            return True

    return False


async def extract_user_id(message: Message) -> Optional[int]:
    
    if getattr(message, "reply_message", None):
        reply_from_id = getattr(message.reply_message, "from_id", None)
        if isinstance(reply_from_id, int) and reply_from_id > 0:
            return reply_from_id
    
    if getattr(message, "fwd_messages", None):
        if len(message.fwd_messages) > 0:
            forward_from_id = getattr(message.fwd_messages[0], "from_id", None)
            if isinstance(forward_from_id, int) and forward_from_id > 0:
                return forward_from_id
    
    text = message.text or ""
    uid = extract_user_id_from_text(text)
    if uid:
        return uid
    return None


async def get_logic(number):
    
    if not number or number < 1:
        return False
    return True


async def check_quit(chat_id=int):
    fetch = await db_execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    if not fetch:
        return False
    
    return await get_logic(fetch[0])

async def getID(arg: str):
    arg = str(arg or "").strip()
    arg_split = arg.split("|")

    if arg_split[0] == arg:
        if arg.startswith("@") and not arg.lower().startswith("@id"):
            try:
                scr_split = await bot.api.utils.resolve_screen_name(arg[1:])
                x = json.loads(scr_split.model_dump_json())
                return int(x["object_id"])
            except:
                pass
        try:
            
            if any(domain in arg for domain in ["vk.com/", "vk.me/", "vk.ru/"]):
                clean_arg = (
                    arg.replace("https://", "")
                    .replace("http://", "")
                    .replace("www.", "")
                )

                for domain in ["vk.com/", "vk.me/", "vk.ru/"]:
                    if domain in clean_arg:
                        clean_arg = clean_arg.split(domain)[1]
                        break
                clean_arg = re.split(r"[/?]", clean_arg)[0]
                scr_split = await bot.api.utils.resolve_screen_name(clean_arg)
                x = json.loads(scr_split.model_dump_json())
                return int(x["object_id"])
        except:
            pass

        
        com_split = arg.split("vk.com/id")
        try:
            if com_split[1].isnumeric():
                return com_split[1]
            else:
                return False
        except:
            
            for domain in ["vk.com/", "vk.me/", "vk.ru/"]:
                if domain in arg:
                    try:
                        screen_split = arg.split(domain)
                        scr_split = await bot.api.utils.resolve_screen_name(screen_split[1])
                        ut_split = str(scr_split).split(" ")
                        obj_split = ut_split[1].split("_id=")
                        if not obj_split[1].isnumeric():
                            return False
                        return obj_split[1]
                    except:
                        return False

    try:
        id_split = arg_split[0].split("id")
        return int(id_split[1])
    except:
        return False        

async def get_registration_date(user_id=int):
    vk_link = f"http://vk.com/foaf.php?id={user_id}"
    with urllib.request.urlopen(vk_link) as response:
        vk_xml = response.read().decode("windows-1251")

    parsed_xml = re.findall(r'created dc:date="(.*)"', vk_xml)
    for item in parsed_xml:
        sp_i = item.split('+')
        str = sp_i[0]  

        PATTERN_IN1 = "%Y-%m-%dT%H:%M:%S"  
        PATTERN_OUT1 = "%B"  

        date1 = datetime.strptime(str, PATTERN_IN1)
        cp_date1 = datetime.strftime(date1, PATTERN_OUT1)

        locales = {"November": "ноября", "October": "октября", "September": "сентября", "August": "августа",
                   "July": "июля", "June": "июня", "May": "мая", "April": "апреля", "March": "марта",
                   "February": "февраля", "January": "января", "December": "декабря"}
        m = locales.get(cp_date1)

        PATTERN_IN = "%Y-%m-%dT%H:%M:%S"  
        PATTERN_OUT = f"%d-ого {m} 20%yг"  

        date = datetime.strptime(str, PATTERN_IN)
        cp_date = datetime.strftime(date, PATTERN_OUT)

    return cp_date

async def fetch_regvk_registration(user_id: int) -> dict:
    vk_url = f"https://vk.com/id{int(user_id)}"
    form_data = {
        "link": vk_url,
        "button": "Определить дату регистрации",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
    }
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.post("https://regvk.com/", data=form_data) as response:
            response.raise_for_status()
            page_text = await response.text()

    page_text = html.unescape(re.sub(r"\s+", " ", page_text))
    date_match = re.search(r"Дата регистрации:\s*([^<]+?)\s*<br", page_text, flags=re.IGNORECASE)
    elapsed_match = re.search(r"Прошло времени:\s*([^<]+?)\s*<", page_text, flags=re.IGNORECASE)
    url_match = re.search(r'Адрес страницы:\s*<a[^>]+href="([^"]+)"', page_text, flags=re.IGNORECASE)

    if not date_match:
        raise ValueError("regvk did not return registration date")

    return {
        "date": date_match.group(1).strip(),
        "elapsed": (elapsed_match.group(1).strip() if elapsed_match else "не удалось определить"),
        "url": (url_match.group(1).strip() if url_match else vk_url),
    }

async def get_string(text=[], arg=int):
    data_string = []
    for i in range(len(text)):
        if i < arg: pass
        else: data_string.append(text[i])
    return_string = " ".join(data_string)
    if return_string == "": return False
    else: return return_string


def get_raw_text_tail(text: str, arg: int = 1):
    """Return text after ``arg`` whitespace-separated tokens, preserving line breaks."""
    source = text or ""
    matches = list(re.finditer(r"\S+", source))
    if len(matches) <= arg:
        return False
    value = source[matches[arg].start():].strip()
    return value or False

def _open_main_sqlite_connection():
    conn = sqlite3.connect(
        'database.db',
        timeout=30,
        check_same_thread=False,
        isolation_level=None,
    )
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as error:
        print(f"[DB] WAL mode failed, fallback to DELETE journal: {error}")
        try:
            cur.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.OperationalError as fallback_error:
            print(f"[DB] DELETE journal fallback failed: {fallback_error}")
    try:
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError as error:
        print(f"[DB] PRAGMA configure error: {error}")
    return conn, cur


database, sql = _open_main_sqlite_connection()
db_lock = asyncio.Lock()



_COUNTRY_MAP_RENDER_SEMAPHORE = asyncio.Semaphore(1)
_COUNTRY_MAP_CACHE_LOCK = asyncio.Lock()
_COUNTRY_MAP_CACHE = {}
_COUNTRY_MAP_INFLIGHT = {}
_COUNTRY_MAP_CACHE_DIR = os.path.join(tempfile.gettempdir(), "country_map_cache")
_COUNTRY_STATE_OPERATION_LOCKS = {}
_COUNTRY_BACKGROUND_TASKS = set()



async def db_execute(query, params=None, fetch='none'):
    """Безопасное выполнение SQL-запроса с блокировкой."""
    async with db_lock:
        for attempt in range(2):
            try:
                if params:
                    sql.execute(query, params)
                else:
                    sql.execute(query)
                if fetch == 'one':
                    return sql.fetchone()
                elif fetch == 'all':
                    return sql.fetchall()
                return None
            except (sqlite3.OperationalError, sqlite3.ProgrammingError) as e:
                print(f"[DB ERROR] {e} | Query: {query}")
                try:
                    database.rollback()
                except:
                    pass
                _reset_sql_cursor()
                if attempt == 0:
                    continue
                return None
            except Exception as e:
                print(f"[DB ERROR] {e} | Query: {query}")
                try:
                    database.rollback()
                except:
                    pass
                _reset_sql_cursor()
                return None


async def ensure_runtime_schema():
    global runtime_schema_ready
    if runtime_schema_ready:
        return
    async with runtime_schema_lock:
        if runtime_schema_ready:
            return
        schema_queries = [
            """
            CREATE TABLE IF NOT EXISTS gbanlist (
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason_gban TEXT NOT NULL,
                datetime_globalban TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS games_blacklist (
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason_gban TEXT NOT NULL,
                datetime_globalban TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS economy_wipe_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subscription_claims (
                user_id INTEGER PRIMARY KEY,
                claimed_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS permanent_prize_effects (
                user_id INTEGER PRIMARY KEY,
                prize_bonus_percent INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS permanent_business_effects (
                user_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                branch_no INTEGER NOT NULL,
                bonus_percent INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, business_key, branch_no)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS globalban (
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason_gban TEXT NOT NULL,
                datetime_globalban TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason_gban TEXT NOT NULL,
                datetime_globalban TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS gamesettings (
                chat_id BIGINT NOT NULL PRIMARY KEY,
                mode INT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS mutesettings (
                chat_id BIGINT NOT NULL PRIMARY KEY,
                mode INT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS photosettings (
                chat_id BIGINT NOT NULL PRIMARY KEY,
                mode INT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS antisliv (
                chat_id INTEGER PRIMARY KEY,
                mode INTEGER DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS modified_chats (
                chat_id INTEGER PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_coowners (
                chat_id INTEGER NOT NULL,
                user_id BIGINT NOT NULL,
                granted_by BIGINT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
            """,
        ]
        for query in schema_queries:
            await _sqlite_write_with_retry(query, tuple())
        runtime_schema_ready = True

def _reset_sql_cursor():
    """Полностью пересоздать соединение и курсор при ошибке базы данных."""
    global sql, database
    try:
        database.rollback()
    except:
        pass
    try:
        sql.close()
    except:
        pass
    try:
        database.close()
    except:
        pass
    try:
        database, sql = _open_main_sqlite_connection()
    except Exception as e:
        print(f"[_reset_sql_cursor] Failed to reset cursor: {e}")

def safe_sql_execute(query, params=None):
    """Безопасный SQL-запрос с пересозданием курсора при ошибке."""
    global sql
    for attempt in range(2):
        try:
            if params:
                sql.execute(query, params)
            else:
                sql.execute(query)
            return
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as e:
            print(f"[SQL ERROR] {e} | Query: {query}")
            _reset_sql_cursor()
            if attempt == 0:
                continue
            raise
        except Exception as e:
            print(f"[SQL ERROR] {e} | Query: {query}")
            _reset_sql_cursor()
            raise
try:
    sql.execute("ALTER TABLE chats ADD COLUMN chat_title TEXT")
    database.commit()
except Exception:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN invite_link TEXT")
    database.commit()
except Exception:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN gzov_disabled INTEGER DEFAULT 0")
    database.commit()
except Exception:
    pass
async def check_chat(chat_id=int):
    row = await db_execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    if row == None: return False
    else: return True


async def delete_chat_from_database(chat_id: int, stop_chat: bool = True) -> int:
    sql.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    table_names = [row[0] for row in sql.fetchall()]
    deleted_tables = 0

    for table_name in table_names:
        try:
            sql.execute(f"PRAGMA table_info({table_name})")
            columns = [column[1] for column in sql.fetchall()]
        except Exception:
            continue

        if "chat_id" not in columns:
            continue

        try:
            sql.execute(f"DELETE FROM {table_name} WHERE chat_id = ?", (chat_id,))
            deleted_tables += 1
        except Exception as error:
            print(f"[delete_chat_from_database] Ошибка очистки таблицы {table_name}: {error}")

    if stop_chat:
        sql.execute("INSERT OR IGNORE INTO banschats (chat_id) VALUES (?)", (chat_id,))
    else:
        sql.execute("DELETE FROM banschats WHERE chat_id = ?", (chat_id,))

    database.commit()
    return deleted_tables
    
sql.execute("""
CREATE TABLE IF NOT EXISTS gbanlist (
    user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    reason_gban TEXT NOT NULL,
    datetime_globalban TEXT NOT NULL
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS games_blacklist (
    user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    reason_gban TEXT NOT NULL,
    datetime_globalban TEXT NOT NULL
)
""")
database.commit()


sql.execute("""
CREATE TABLE IF NOT EXISTS gsync_list (
    owner_id INTEGER,
    table_name TEXT
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    type TEXT,
    value INTEGER,
    creator_id INTEGER,
    uses_left INTEGER,
    max_uses INTEGER DEFAULT 0
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS promoused (
    user_id INTEGER,
    code TEXT
)
""")
database.commit()

try:
    sql.execute("ALTER TABLE promocodes ADD COLUMN max_uses INTEGER DEFAULT 0")
    database.commit()
except:
    pass

sql.execute("""
CREATE TABLE IF NOT EXISTS tech_chat_settings (
    chat_id BIGINT PRIMARY KEY,
    enabled BIGINT DEFAULT 0
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS tech_permissions (
    chat_id BIGINT,
    user_id BIGINT,
    level BIGINT,
    UNIQUE(chat_id, user_id)
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS globalban (
    user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    reason_gban TEXT NOT NULL,
    datetime_globalban TEXT NOT NULL
)
""")
database.commit()

sql.execute("""CREATE TABLE IF NOT EXISTS rules (
    chat_id INTEGER PRIMARY KEY,
    description TEXT
)""")
database.commit()

sql.execute("""CREATE TABLE IF NOT EXISTS info (
    chat_id INTEGER PRIMARY KEY,
    description TEXT
)""")
database.commit()

sql.execute("""CREATE TABLE IF NOT EXISTS antisliv (
    chat_id INTEGER PRIMARY KEY,
    mode INTEGER DEFAULT 0
)""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    reason_gban TEXT NOT NULL,
    datetime_globalban TEXT NOT NULL
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS protection (
    chat_id BIGINT NOT NULL PRIMARY KEY,
    mode INT NOT NULL
);
""")

database.commit()



COMMUNITY_SPAM_THRESHOLD = 3
COMMUNITY_SPAM_WINDOW_HOURS = 24

sql.execute("""
CREATE TABLE IF NOT EXISTS community_spam_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    community_id BIGINT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""")
sql.execute("""
CREATE INDEX IF NOT EXISTS idx_community_spam_attempts_user_time
ON community_spam_attempts(user_id, created_at);
""")
sql.execute("""
CREATE TABLE IF NOT EXISTS community_spammers (
    user_id BIGINT PRIMARY KEY,
    strikes INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT NOT NULL DEFAULT 'Добавление сторонних сообществ'
);
""")
database.commit()

def _is_community_spammer(user_id: int) -> bool:
    try:
        if int(user_id) <= 0:
            return False
        row = sql.execute(
            "SELECT 1 FROM community_spammers WHERE user_id = ? LIMIT 1",
            (int(user_id),),
        ).fetchone()
        return bool(row)
    except Exception as exc:
        print(f"[PROTECTION] spammer lookup failed user={user_id}: {exc}")
        return False

def _record_community_spam_attempt(user_id: int, chat_id: int, community_id: int):
    """Записывает нарушение и возвращает (нарушений_за_24ч, стал_спамером)."""
    uid = int(user_id)
    if uid <= 0:
        return 0, False
    try:
        sql.execute(
            "INSERT INTO community_spam_attempts(user_id, chat_id, community_id) VALUES(?, ?, ?)",
            (uid, int(chat_id), int(community_id)),
        )
        
        sql.execute(
            "DELETE FROM community_spam_attempts WHERE datetime(created_at) < datetime('now', '-7 days')"
        )
        row = sql.execute(
            """SELECT COUNT(*) FROM community_spam_attempts
               WHERE user_id = ?
                 AND datetime(created_at) >= datetime('now', ?)""",
            (uid, f'-{int(COMMUNITY_SPAM_WINDOW_HOURS)} hours'),
        ).fetchone()
        strikes = int(row[0] or 0) if row else 0
        already = _is_community_spammer(uid)
        became_spammer = False
        if strikes >= int(COMMUNITY_SPAM_THRESHOLD):
            sql.execute(
                """INSERT INTO community_spammers(user_id, strikes, first_seen_at, last_seen_at, reason)
                   VALUES(?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Частое добавление сторонних сообществ')
                   ON CONFLICT(user_id) DO UPDATE SET
                       strikes = MAX(community_spammers.strikes, excluded.strikes),
                       last_seen_at = CURRENT_TIMESTAMP""",
                (uid, strikes),
            )
            became_spammer = not already
        database.commit()
        return strikes, became_spammer
    except Exception as exc:
        print(f"[PROTECTION] spam attempt write failed user={uid}: {exc}")
        try:
            database.rollback()
        except Exception:
            pass
        return 0, False

async def _protection_kick(chat_id: int, member_id: int) -> bool:
    try:
        await bot.api.messages.remove_chat_user(int(chat_id), int(member_id))
        return True
    except Exception as exc:
        print(f"[PROTECTION] kick failed chat={chat_id} member={member_id}: {exc}")
        return False

sql.execute("""
CREATE TABLE IF NOT EXISTS mutesettings (
    chat_id BIGINT NOT NULL PRIMARY KEY,
    mode INT NOT NULL
);
""")

database.commit()


sql.execute("""
CREATE TABLE IF NOT EXISTS economy (
    user_id INTEGER,
    target_id INTEGER,
    amount INTEGER,
    log TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")
database.commit()
try:
    sql.execute("ALTER TABLE economy ADD COLUMN created_at TEXT")
    database.commit()
except Exception:
    pass
try:
    sql.execute("UPDATE economy SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (msk_now_str(),))
    database.commit()
except Exception:
    pass


GLOBAL_REPUTATION_CHAT_ID = 0
sql.execute("""
CREATE TABLE IF NOT EXISTS reputation (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
""")
sql.execute("""
CREATE TABLE IF NOT EXISTS reputation_votes (
    chat_id INTEGER NOT NULL,
    voter_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    direction INTEGER NOT NULL CHECK(direction IN (-1, 1)),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id, voter_id, target_id, direction)
)
""")


try:
    sql.execute("""
        INSERT OR IGNORE INTO reputation_votes
            (chat_id, voter_id, target_id, direction, created_at)
        SELECT ?, voter_id, target_id, direction, MIN(created_at)
        FROM reputation_votes
        WHERE chat_id <> ?
        GROUP BY voter_id, target_id, direction
    """, (GLOBAL_REPUTATION_CHAT_ID, GLOBAL_REPUTATION_CHAT_ID))
    sql.execute(
        "DELETE FROM reputation WHERE chat_id = ?",
        (GLOBAL_REPUTATION_CHAT_ID,),
    )
    sql.execute("""
        INSERT INTO reputation (chat_id, user_id, value)
        SELECT ?, target_id, SUM(direction)
        FROM reputation_votes
        WHERE chat_id = ?
        GROUP BY target_id
    """, (GLOBAL_REPUTATION_CHAT_ID, GLOBAL_REPUTATION_CHAT_ID))
    database.commit()
except Exception as reputation_migration_error:
    database.rollback()
    print(f"[GLOBAL REPUTATION MIGRATION ERROR] {reputation_migration_error}")

sql.execute("""
CREATE TABLE IF NOT EXISTS banschats (
    chat_id INTEGER PRIMARY KEY
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS bugsusers (
    user_id INTEGER,
    bug TEXT,
    datetime TEXT,
    bug_counts_user INTEGER
)
""")
database.commit()


sql.execute("""
CREATE TABLE IF NOT EXISTS servers_list (
    owner_id INTEGER,
    server_number TEXT,
    table_name TEXT
)
""")
database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS server_links(
    server_id INTEGER,
    chat_id INTEGER,
    chat_title TEXT
)
""")
database.commit()

try:
    sql.execute("PRAGMA table_info(server_links)")
    server_link_columns = {str(row[1]) for row in sql.fetchall()}
    if "owner_id" not in server_link_columns:
        sql.execute("ALTER TABLE server_links ADD COLUMN owner_id INTEGER")
    if "server_number" not in server_link_columns:
        sql.execute("ALTER TABLE server_links ADD COLUMN server_number TEXT")
    sql.execute("CREATE INDEX IF NOT EXISTS idx_server_links_chat_id ON server_links(chat_id)")
    sql.execute("CREATE INDEX IF NOT EXISTS idx_server_links_owner_server ON server_links(owner_id, server_number)")
    database.commit()
except Exception as error:
    print(f"[server_links schema] migration error: {error}")

sql.execute("""
CREATE TABLE IF NOT EXISTS gamesettings (
    chat_id BIGINT NOT NULL PRIMARY KEY,
    mode INT NOT NULL
);
""")

database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS photosettings (
    chat_id BIGINT NOT NULL PRIMARY KEY,
    mode INT NOT NULL
);
""")

database.commit()

sql.execute("""
CREATE TABLE IF NOT EXISTS exchange_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id BIGINT,
    target_id BIGINT,
    status TEXT,
    log TEXT,
    created_at TEXT
);
""")

database.commit()

try:
    
    sql.execute("PRAGMA table_info(ban_words)")
    columns = [col[1] for col in sql.fetchall()]

    
    if "word" not in columns or "creator_id" not in columns or "time" not in columns:
        print("[INIT] Пересоздание таблицы ban_words...")
        sql.execute("DROP TABLE IF EXISTS ban_words")
        sql.execute("""
        CREATE TABLE IF NOT EXISTS ban_words (
            word TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            time TEXT NOT NULL
        )
        """)
        database.commit()
        print("[INIT] Таблица ban_words успешно пересоздана.")
except Exception as e:
    print(f"[INIT] Ошибка при проверке таблицы ban_words: {e}")    

async def new_chat(chat_id: int, peer_id: int, owner_id: int, chat_type: str = "def"):
    
    sql.execute("PRAGMA table_info(chats)")
    columns = [col[1] for col in sql.fetchall()]

    chat_title = f"Беседа "
    resolved_owner_id = int(owner_id)
    try:
        response = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
        if response.items:
            chat_settings = response.items[0].chat_settings
            live_title = getattr(chat_settings, "title", None)
            if live_title:
                chat_title = live_title
            live_owner_id = getattr(chat_settings, "owner_id", None)
            if live_owner_id is not None:
                resolved_owner_id = int(live_owner_id)
    except Exception as error:
        print(f"[new_chat] Не удалось получить название чата {chat_id}: {error}")

    
    insert_columns = ["chat_id", "peer_id", "owner_id"]
    insert_values = [chat_id, peer_id, resolved_owner_id]

    if "welcome_msg" in columns:
        insert_columns.append("welcome_msg")
        insert_values.append("Добро пожаловать, уважаемый %i пользователь!")

    if "type" in columns:
        insert_columns.append("type")
        insert_values.append(chat_type)

    if "chat_title" in columns:
        insert_columns.append("chat_title")
        insert_values.append(chat_title)

    sql.execute(f"INSERT INTO chats ({', '.join(insert_columns)}) VALUES ({', '.join(['?']*len(insert_values))})", insert_values)

    
    sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS nicks_{chat_id} (user_id BIGINT, nick TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS banwords_{chat_id} (banword TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warns_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS mutes_{chat_id} (user_id BIGINT, moder TEXT, reason TEXT, date BIGINT, date_string TEXT, time BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS mutelogs_{chat_id} (user_id BIGINT, moder_id BIGINT, reason TEXT, date BIGINT, date_string TEXT, mute_time BIGINT, status TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS bans_{chat_id} (user_id BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS messages_{chat_id} (user_id BIGINT, date BIGINT, date_string TEXT, message_id BIGINT, cmid BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warnhistory_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS punishments_{chat_id} (user_id BIGINT, date TEXT);")

    database.commit()
      
async def get_global_manager_level(user_id: int) -> int:
    try:
        fetch = await db_execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,), fetch='one')
        return int(fetch[0]) if fetch else 0
    except Exception as e:
        print(f"Ошибка при получении глобальной роли для user_id={user_id}: {e}")
        return 0


async def can_use_admin_grant_commands(user_id: int, chat_id: int, min_role: int = 12) -> bool:
    return await _has_role_level(user_id, chat_id, min_role)


async def can_reset_rebirth(user_id: int, chat_id: int) -> bool:
    
    
    
    
    
    return await _has_role_level(user_id, chat_id, GLOBAL_TECH_CURATOR_ROLE_LEVEL)

async def _is_modified_chat(chat_id: int) -> bool:
    fetch = await db_execute("SELECT 1 FROM modified_chats WHERE chat_id = ?", (chat_id,), fetch='one')
    return bool(fetch)


async def _can_use_command(user_id: int, chat_id: int, standard_min: int) -> bool:
    role = await get_role(user_id, chat_id)
    return _role_can_satisfy_standard_min(role, standard_min)


async def _get_primary_chat_owner_id(chat_id: int) -> int:
    row = await db_execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    return int(row[0]) if row and row[0] is not None else 0


async def _is_primary_chat_owner(user_id: int, chat_id: int) -> bool:
    return int(user_id) == await _get_primary_chat_owner_id(chat_id)


async def _is_additional_chat_owner(user_id: int, chat_id: int) -> bool:
    row = await db_execute(
        "SELECT 1 FROM chat_coowners WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id), fetch='one'
    )
    return bool(row)


async def _is_any_chat_owner(user_id: int, chat_id: int) -> bool:
    return await _is_primary_chat_owner(user_id, chat_id) or await _is_additional_chat_owner(user_id, chat_id)


async def _is_chat_owner_or_global(user_id: int, chat_id: int) -> bool:
    role = await get_role(user_id, chat_id)
    if _is_encoded_global_role(role):
        return True
    return await _is_any_chat_owner(user_id, chat_id)


async def _has_role_level(user_id: int, chat_id: int, min_level: int) -> bool:
    role = await get_role(user_id, chat_id)
    return _role_can_satisfy_standard_min(role, min_level)


async def _get_custom_role_name(chat_id: int, level: int) -> str:
    real_level = 100 if int(level) == 200 else (level - 100 if level > 100 else level)
    try:
        fetch = await db_execute(
            f"SELECT name FROM custom_roles_{chat_id} WHERE level = ?",
            (real_level,), fetch='one'
        )
        if fetch:
            return fetch[0]
        return "Пользователь" if real_level == 0 else f"Уровень {real_level}"
    except:
        return "Пользователь" if real_level == 0 else f"Уровень {real_level}"


async def get_role_display_name(user_id: int, chat_id: int) -> str:
    global_level = await get_global_manager_level(user_id)
    if global_level:
        return GLOBAL_LEVEL_NAMES.get(global_level, "Глобальная роль")

    role = await get_role(user_id, chat_id)

    if await _is_modified_chat(chat_id):
        if role == 200:
            return await _get_custom_role_name(chat_id, 200)
        if role >= 100:
            return await _get_custom_role_name(chat_id, role)
        real_level = role
        try:
            fetch = await db_execute(
                f"SELECT name FROM custom_roles_{chat_id} WHERE level = ?",
                (real_level,), fetch='one'
            )
            if fetch:
                return fetch[0]
        except:
            pass
        return "Пользователь"

    roles = {
        0: "Пользователь",
        1: "Модератор",
        2: "Старший модератор",
        3: "Администратор",
        4: "Старший администратор",
        5: "Зам. спец администратора",
        6: "Спец администратор",
        7: "Владелец беседы",
        8: "Медиа Партнер",
        9: "Технический специалист",
        10: "Заместитель директора",
        11: "Осн. заместитель директора",
        12: "Куратор техспециалистов",
        13: "Зам. Гл. Тех. Специалиста",
        14: "Главный Тех. Специалист",
        15: "Директор бота",
        16: GLOBAL_QUALITY_CONTROL_NAME,
        17: "Разработчик бота",
    }
    result = roles.get(role, "Пользователь")
    return str(result).strip() if result not in (None, "", "None") else "Пользователь"


async def resolve_log_role_display(user_id: int, chat_id: int, stored_role=None) -> str:
    """Возвращает корректное имя роли для вывода в логах.

    Приоритет: глобальная роль -> модифицированная роль -> обычная роль.
    Поле role из старой записи используется лишь как последний безопасный fallback.
    """
    try:
        value = await get_role_display_name(int(user_id), int(chat_id))
        if value is not None:
            value = str(value).strip()
            if value and value.lower() not in {"none", "null", "—"}:
                return value
    except Exception as exc:
        print(f"[LOG ROLE RESOLVE ERROR] user={user_id} chat={chat_id}: {exc}")

    if stored_role is not None:
        stored = str(stored_role).strip()
        if stored and stored.lower() not in {"none", "null", "—"}:
            
            try:
                level = int(stored)
                standard_names = {
                    0: "Пользователь", 1: "Модератор", 2: "Старший модератор",
                    3: "Администратор", 4: "Старший администратор",
                    5: "Зам. спец администратора", 6: "Спец администратор",
                    7: "Владелец беседы", 8: "Медиа Партнер",
                    9: "Технический специалист", 10: "Заместитель директора",
                    11: "Осн. заместитель директора", 12: "Куратор техспециалистов",
                    13: "Зам. Гл. Тех. Специалиста", 14: "Главный Тех. Специалист",
                    15: "Директор бота", 16: GLOBAL_QUALITY_CONTROL_NAME,
                    17: "Разработчик бота",
                }
                return standard_names.get(level, f"Уровень {level}")
            except (TypeError, ValueError):
                return stored

    return "Пользователь"


async def get_role(user_id: int, chat_id: int) -> int:
    """Возвращает числовой уровень роли пользователя. Чем выше число, тем выше роль.
    0  - обычный пользователь
    1  - модератор
    2  - старший модератор
    3  - админ
    4  - старший админ
    5  - зам. спецадмина
    6  - спецадмин
    7  - владелец чата
    8  - медиа партнер
    9  - технический специалист
    10 - заместитель директора
    11 - основной заместитель директора
    12 - куратор технических специалистов
    13 - директор бота
    16 - контроль качества
    17 - разработчик бота
    """
    try:
        global_level = await get_global_manager_level(user_id)
        if global_level:
            if global_level in GLOBAL_ROLE_LEVELS:
                return GLOBAL_ROLE_OFFSET + global_level

        
        
        if await _is_any_chat_owner(user_id, chat_id):
            if await _is_modified_chat(chat_id):
                return 200
            return 7

        await db_execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
        
        
        
        
        fetch = await db_execute(
            f"SELECT MAX(level) FROM permissions_{chat_id} WHERE user_id = ?",
            (user_id,), fetch='one'
        )
        if fetch and fetch[0] is not None:
            level = int(fetch[0])
            if await _is_modified_chat(chat_id):
                return 100 + level if 1 <= level <= 99 else 0
            return level if 0 <= level <= 7 else 0
        
    except Exception as e:
        print(f"Ошибка при получении роли для user_id={user_id}, chat_id={chat_id}: {e}")
    
    return 0  


async def is_tech_chat(chat_id: int) -> bool:
    sql.execute("SELECT enabled FROM tech_chat_settings WHERE chat_id = ?", (chat_id,))
    row = sql.fetchone()
    return bool(row and int(row[0]) == 1)


async def set_tech_chat(chat_id: int, enabled: int = 1):
    sql.execute(
        "INSERT INTO tech_chat_settings (chat_id, enabled) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled",
        (chat_id, int(enabled)),
    )
    database.commit()


async def get_tech_role(user_id: int, chat_id: int) -> int:
    sql.execute("SELECT level FROM tech_permissions WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    row = sql.fetchone()
    return int(row[0]) if row else 0


async def set_tech_role(user_id: int, chat_id: int, level: int):
    if int(level) <= 0:
        sql.execute("DELETE FROM tech_permissions WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    else:
        sql.execute(
            "INSERT INTO tech_permissions (chat_id, user_id, level) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET level = excluded.level",
            (chat_id, user_id, int(level)),
        )
    database.commit()


async def get_tech_staff(chat_id: int):
    sql.execute("SELECT user_id, level FROM tech_permissions WHERE chat_id = ? ORDER BY level DESC, user_id ASC", (chat_id,))
    rows = sql.fetchall()
    result = {level: [] for level in TECH_ROLE_NAMES.keys()}
    for user_id, level in rows:
        if int(level) in result:
            result[int(level)].append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
    return result


async def check_target_permission(initiator_id: int, target_id: int, chat_id: int) -> bool:
    """Проверяет, может ли инициатор применить команду к цели.
    
    Возвращает True, если роль инициатора строго выше роли цели.
    Возвращает False, если роли равны или роль цели выше.
    """
    initiator_role = await get_role(initiator_id, chat_id)
    target_role = await get_role(target_id, chat_id)
    
    return _role_hierarchy_value(initiator_role) > _role_hierarchy_value(target_role)

async def get_warns(user_id=int, chat_id=int):
    fetch = await db_execute(f"SELECT count FROM warns_{chat_id} WHERE user_id = ?", (user_id,), fetch='one')
    if fetch == None: return 0
    else: return fetch[0]

def paginate_list(items, page, per_page=20):
    page = max(1, int(page or 1))
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], (len(items) + per_page - 1) // per_page

def make_nav_keyboard(base_cmd, page, chat_context_id):
    kb = Keyboard(inline=True)
    prev_payload = {"command": f"{base_cmd}minus", "page": page, "chatId": chat_context_id}
    next_payload = {"command": f"{base_cmd}plus", "page": page, "chatId": chat_context_id}
    kb.add(Callback("⏪", prev_payload), color=KeyboardButtonColor.NEGATIVE)
    kb.add(Callback("⏩", next_payload), color=KeyboardButtonColor.POSITIVE)
    return kb


async def get_gsync_chats(chat_id):
    sql.execute("SELECT owner_id, table_name FROM gsync_list")
    gsyncs = sql.fetchall()

    for owner_id, table_name in gsyncs:
        try:
            sql.execute(f"SELECT chat_id FROM {table_name} WHERE chat_id = ?", (chat_id,))
            if sql.fetchone():
                sql.execute(f"SELECT chat_id FROM {table_name}")
                chats = sql.fetchall()
                return [c[0] for c in chats]
        except:
            continue
    return None


async def get_gsync_table(chat_id):
    sql.execute("SELECT owner_id, table_name FROM gsync_list")
    gsyncs = sql.fetchall()

    for owner_id, table_name in gsyncs:
        try:
            sql.execute(f"SELECT chat_id FROM {table_name} WHERE chat_id = ?", (chat_id,))
            if sql.fetchone():
                return {"owner": owner_id, "table": table_name}
        except:
            continue
    return None    

async def get_user_name(user_id: int, chat_id: int | None = None) -> str:
    user_id = int(user_id)
    if user_id < 0:
        group_name = await get_vk_entity_name(user_id)
        if group_name and group_name != "Не удалось получить имя":
            return group_name
        return "ADMIN MANAGER"

    
    if chat_id is not None:
        try:
            sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
            fetch = sql.fetchone()
            if fetch and fetch[0]:
                return fetch[0]
        except:
            pass  

    
    try:
        info = await bot.api.users.get(user_ids=user_id)
        if info and len(info) > 0:
            return f"{info[0].first_name} {info[0].last_name}"
    except:
        pass

    if chat_id is not None and int(chat_id or 0) > 0:
        try:
            members = await bot.api.messages.get_conversation_members(
                peer_id=2000000000 + int(chat_id),
                fields=["first_name", "last_name"],
            )
            data = json.loads(members.model_dump_json())
            for profile in data.get("profiles", []):
                if int(profile.get("id", 0) or 0) == user_id:
                    first_name = str(profile.get("first_name", "") or "").strip()
                    last_name = str(profile.get("last_name", "") or "").strip()
                    full_name = f"{first_name} {last_name}".strip()
                    if full_name:
                        return full_name
        except:
            pass

    
    return str(user_id)


async def format_vk_entity_link(entity_id: int, chat_id: int | None = None) -> str:
    return format_vk_link(entity_id, await get_user_name(entity_id, chat_id))


def _get_media_attr(media_obj, attr: str, default=None):
    if media_obj is None:
        return default
    if isinstance(media_obj, dict):
        return media_obj.get(attr, default)
    return getattr(media_obj, attr, default)


def _build_vk_attachment(kind: str, media_obj) -> str:
    owner_id = _get_media_attr(media_obj, "owner_id", 0)
    media_id = _get_media_attr(media_obj, "id", 0)
    access_key = _get_media_attr(media_obj, "access_key", "")
    if not owner_id or not media_id:
        return ""
    attachment = f"{kind}{owner_id}_{media_id}"
    if access_key:
        attachment += f"_{access_key}"
    return attachment


def extract_message_media_attachments(message) -> list[str]:
    attachments = []
    for attach in (getattr(message, "attachments", None) or []):
        raw_type = getattr(attach, "type", "") if not isinstance(attach, dict) else attach.get("type", "")
        atype = str(getattr(raw_type, "value", raw_type) or "").lower()
        photo_obj = getattr(attach, "photo", None) if not isinstance(attach, dict) else attach.get("photo")
        video_obj = getattr(attach, "video", None) if not isinstance(attach, dict) else attach.get("video")

        attachment = ""
        if photo_obj is not None and ("photo" in atype or not atype):
            attachment = _build_vk_attachment("photo", photo_obj)
        elif video_obj is not None and ("video" in atype or not atype):
            attachment = _build_vk_attachment("video", video_obj)

        if attachment and attachment not in attachments:
            attachments.append(attachment)
    return attachments
    

async def clear_all_warns(chat_id: int) -> int:
    
    sql.execute(f"SELECT DISTINCT user_id FROM warns_{chat_id}")
    users = sql.fetchall()

    if not users:
        return 0  

    count = len(users)

    
    sql.execute(f"DELETE FROM warns_{chat_id}")
    database.commit()

    return count


async def messageslist(user_id=None, chat_id=None):
    if user_id is not None and chat_id is not None:
        sql.execute("SELECT * FROM messages_today WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
    elif user_id is not None:
        sql.execute("SELECT * FROM messages_today WHERE user_id = ?", (user_id,))
    elif chat_id is not None:
        sql.execute("SELECT * FROM messages_today WHERE chat_id = ?", (chat_id,))
    else:
        sql.execute("SELECT * FROM messages_today")

    fetch = sql.fetchall()
    messages = []
    for _row in fetch:
        messages.append("None")

    return messages
    
async def is_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = {user_id}")
    if sql.fetchone() == None: return False
    else: return True

async def setnick(user_id=int, chat_id=int, nick=str):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = {user_id}")
    if sql.fetchone() == None:
        sql.execute(f"INSERT INTO nicks_{chat_id} VALUES (?, ?)", (user_id, nick))
        database.commit()
    else:
        sql.execute(f"UPDATE nicks_{chat_id} SET nick = ? WHERE user_id = ?", (nick, user_id))
        database.commit()

async def rnick(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id} WHERE user_id = {user_id}")
    database.commit()

async def get_acc(chat_id=int, nick=str):
    normalized_nick = str(nick or "").strip().lower()
    sql.execute(
        f"SELECT user_id FROM nicks_{chat_id} WHERE LOWER(TRIM(nick)) = ?",
        (normalized_nick,),
    )
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]

async def get_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = {user_id}")
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]


def _sqlite_write_sync(query: str, params: tuple):
    conn = sqlite3.connect(
        "database.db",
        timeout=30,
        check_same_thread=False,
        isolation_level=None,
    )
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute(query, params)
        conn.commit()
    finally:
        conn.close()


async def _sqlite_write_with_retry(query: str, params: tuple, retries: int = 6, delay: float = 0.25):
    last_error = None
    for attempt in range(retries):
        try:
            await asyncio.to_thread(_sqlite_write_sync, query, params)
            return
        except sqlite3.OperationalError as e:
            last_error = e
            if "locked" not in str(e).lower() or attempt == retries - 1:
                raise
            await asyncio.sleep(delay * (attempt + 1))
    if last_error:
        raise last_error

async def log_economy(user_id=None, target_id=None, amount=None, log=None):
    try:
        if amount is not None and abs(int(amount)) > 9_000_000_000_000_000_000:
            amount = None
        await _sqlite_write_with_retry(
            "INSERT INTO economy (user_id, target_id, amount, log, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, target_id, amount, log, msk_now_str()),
        )
        print(f"[ECONOMY LOG] {user_id} -> {target_id} | {amount} | {log}")
    except Exception as e:
        print(f"[ECONOMY LOG ERROR] {e}")       
        
def _change_reputation_sync(chat_id: int, voter_id: int, target_id: int, direction: int):
    """Атомарно изменяет глобальную репутацию и возвращает (успешно, новое_значение)."""
    global_chat_id = int(GLOBAL_REPUTATION_CHAT_ID)
    conn = sqlite3.connect("database.db", timeout=30, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO reputation_votes (chat_id, voter_id, target_id, direction, created_at) VALUES (?, ?, ?, ?, ?)",
                (global_chat_id, int(voter_id), int(target_id), int(direction), msk_now_str()),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT value FROM reputation WHERE chat_id = ? AND user_id = ?",
                (global_chat_id, int(target_id)),
            ).fetchone()
            conn.rollback()
            return False, int(row[0]) if row else 0

        conn.execute(
            "INSERT INTO reputation (chat_id, user_id, value) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET value = value + excluded.value",
            (global_chat_id, int(target_id), int(direction)),
        )
        row = conn.execute(
            "SELECT value FROM reputation WHERE chat_id = ? AND user_id = ?",
            (global_chat_id, int(target_id)),
        ).fetchone()
        conn.commit()
        return True, int(row[0]) if row else 0
    finally:
        conn.close()


async def change_reputation(chat_id: int, voter_id: int, target_id: int, direction: int):
    
    return await asyncio.to_thread(
        _change_reputation_sync, int(chat_id), int(voter_id), int(target_id), int(direction)
    )


async def get_reputation(chat_id: int, user_id: int) -> int:
    row = await db_execute(
        "SELECT value FROM reputation WHERE chat_id = ? AND user_id = ?",
        (int(GLOBAL_REPUTATION_CHAT_ID), int(user_id)),
        fetch='one',
    )
    return int(row[0]) if row else 0


async def get_reputation_top(chat_id: int, limit: int = 20):
    rows = await db_execute(
        "SELECT user_id, value FROM reputation "
        "WHERE chat_id = ? AND value > 0 "
        "ORDER BY value DESC, user_id ASC LIMIT ?",
        (int(GLOBAL_REPUTATION_CHAT_ID), int(limit)),
        fetch='all',
    )
    return rows or []


async def chats_log(user_id=None, target_id=None, role=None, log=None, chat_id=None):
    try:
        resolved_chat_id = chat_id if chat_id is not None else _CURRENT_LOG_CHAT_ID.get()
        await _sqlite_write_with_retry(
            "INSERT INTO logchats (user_id, target_id, role, log, created_at, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, target_id, role, log, msk_now_str(), resolved_chat_id),
        )
        print(f"[CHATS LOG] chat={resolved_chat_id} | {user_id} -> {target_id} | {role} | {log}")
    except Exception as e:
        print(f"[CHATS LOG ERROR] {e}")


async def _read_moderation_logs(chat_ids: list[int], target_id: int | None = None):
    """Безопасно читает модерационные логи через отдельное SQLite-подключение.

    Глобальный курсор ``sql`` может быть занят другой командой в тот же момент,
    из-за чего VК-обработчик завершался без ответа. Здесь используется db_execute,
    который открывает независимое подключение и корректно работает конкурентно.
    """
    normalized = sorted({int(cid) for cid in chat_ids if cid is not None})
    if not normalized:
        return []

    placeholders = ",".join("?" for _ in normalized)
    params: list[int] = list(normalized)
    query = (
        "SELECT user_id, target_id, role, log, created_at, chat_id "
        f"FROM logchats WHERE chat_id IN ({placeholders})"
    )
    if target_id is not None:
        
        query += " AND (user_id = ? OR target_id = ?)"
        params.extend((int(target_id), int(target_id)))
    query += " ORDER BY rowid DESC"

    rows = await db_execute(query, tuple(params), fetch='all')
    return rows or []


async def _format_moderation_log_page(logs, page: int, context_chat_id: int, show_chat: bool = False):
    per_page = MAX_LOGS
    total = len(logs)
    max_page = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), max_page))
    start = (page - 1) * per_page
    selected = logs[start:start + per_page]
    formatted = []
    for idx, entry in enumerate(selected, start=start + 1):
        u_id, t_id, _stored_role, log_text, created_at, entry_chat_id = entry
        role_chat_id = int(entry_chat_id or context_chat_id)
        try:
            u_name = await get_user_name(int(u_id), role_chat_id)
        except Exception:
            u_name = str(u_id)
        if t_id:
            try:
                t_name = await get_user_name(int(t_id), role_chat_id)
                t_display = f"@id{t_id} ({t_name})"
            except Exception:
                t_display = f"@id{t_id}"
        else:
            t_display = "—"
        try:
            role_display = await resolve_log_role_display(int(u_id), role_chat_id, _stored_role)
        except Exception:
            role_display = "Пользователь"
        chat_display = ""
        if show_chat:
            try:
                chat_title, _ = await get_chat_display_info(role_chat_id)
                chat_display = f" | Беседа: {chat_title} (№{role_chat_id})"
            except Exception:
                chat_display = f" | Беседа: №{role_chat_id}"
        formatted.append(
            f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {role_display or 'Пользователь'}"
            f"{chat_display} | Дата: {created_at or 'Дата не записана'} | Лог: {log_text or '—'}"
        )
    return formatted, page, max_page


async def exchange_log(user_id=None, target_id=None, status=None, log=None):
    try:
        await _sqlite_write_with_retry(
            "INSERT INTO exchange_logs (user_id, target_id, status, log, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, target_id, status, log, msk_now_str()),
        )
        print(f"[EXCHANGE LOG] {user_id} -> {target_id} | {status} | {log}")
    except Exception as e:
        print(f"[EXCHANGE LOG ERROR] {e}")

async def nlist(chat_id: int, page: int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    if not fetch:
        return []

    nicks = []
    gi = 0
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nicks = open_file.get('nicks_max', 20)

    start = (page - 1) * max_nicks
    end = page * max_nicks

    for i in fetch:
        if gi < start:
            gi += 1
            continue
        if gi >= end:
            break

        info = await bot.api.users.get(user_ids=i[0])
        if info and len(info) > 0:
            name = f"{info[0].first_name} {info[0].last_name}"
        else:
            name = "Ошибка"

        nicks.append(f"{gi+1}. @id{i[0]} ({name}) -- {i[1]}")
        gi += 1

    return nicks 

async def nonick(chat_id=int, page=int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    nicks = []
    for i in fetch:
        nicks.append(i[0])

    gi = 0
    nonick = []
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nonick = open_file['nonick_max']
    users = await bot.api.messages.get_conversation_members(peer_id=2000000000+chat_id)
    users = json.loads(users.model_dump_json())
    for i in users["profiles"]:
        if not i['id'] in nicks:
            gi = gi + 1
            if page*max_nonick >= gi and page*max_nonick-max_nonick < gi:
                nonick.append(f"{gi}) @id{i['id']} ({i['first_name']} {i['last_name']})")

    return nonick

async def warn(chat_id=int, user_id=int, moder=int, reason=str):
    actualy_warns = await get_warns(user_id, chat_id)
    date = time.time()
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO warnhistory_{chat_id} VALUES (?, {actualy_warns+1}, ?, ?, {date}, '{date_string}')",(user_id, moder, reason))
    database.commit()
    if actualy_warns < 1:
        sql.execute(f"INSERT INTO warns_{chat_id} VALUES (?, 1, ?, ?, {date}, '{date_string}')", (user_id, moder, reason))
        database.commit()
        return 1
    else:
        sql.execute(f"UPDATE warns_{chat_id} SET user_id = ?, count = ?, moder = ?, reason = ?, date = {date}, date_string = '{date_string}' WHERE user_id = {user_id}", (user_id, actualy_warns+1, moder, reason))
        database.commit()
        return actualy_warns+1

async def clear_warns(chat_id=int, user_id=int):
    sql.execute(f"DELETE FROM warns_{chat_id} WHERE user_id = {user_id}")
    database.commit()

async def unwarn(chat_id=int, user_id=int):
    warns = await get_warns(user_id, chat_id)
    if warns < 2: await clear_warns(chat_id, user_id)
    else:
        sql.execute(f"UPDATE warns_{chat_id} SET count = {warns-1} WHERE user_id = {user_id}")
        database.commit()

    return warns-1

async def gwarn(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id} WHERE user_id = {user_id}")
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return {
            'count': fetch[1],
            'moder': fetch[2],
            'reason': fetch[3],
            'time': fetch[5]
        }

async def warnhistory(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warnhistory_{chat_id} WHERE user_id = {user_id}")
    fetch = sql.fetchall()
    warnhistory_mass = []
    gi = 0
    if fetch == None: return False
    else:
        for i in fetch:
            gi = gi + 1
            warnhistory_mass.append(f"{gi}) @id{i[2]} (Модератор) | {i[3]} | {i[5]}")

    return warnhistory_mass

async def warnlist(chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id}")
    fetch = sql.fetchall()
    warns = []
    gi = 0
    for i in fetch:
        gi = gi + 1
        warns.append(f"{gi}) @id{i[0]} (Пользователь) | {i[3]} | @id{i[2]} (Модератор) | {i[1]}/3 | {i[5]}")

    if fetch == None: return False
    return warns

async def staff(chat_id: int):
    
    sql.execute(f"SELECT * FROM permissions_{chat_id}")
    fetch = sql.fetchall()
    moders = []
    stmoders = []
    admins = []
    stadmins = []
    zamspecadm = []
    specadm = []
    testers = []

    if fetch:
        for i in fetch:
            level = i[1]
            user_id = i[0]
            if level == 1: moders.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 2: stmoders.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 3: admins.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 4: stadmins.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 5: zamspecadm.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 6: specadm.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))
            elif level == 12: testers.append(format_vk_link(user_id, await get_user_name(user_id, chat_id)))

    
    sql.execute("SELECT user_id, level FROM global_managers WHERE level IN (1,2,3,4,5,6,7,8,9,10)")
    global_fetch = sql.fetchall()
    bloggers = []
    tech_specs = []
    tech_curators = []
    zamruk = []
    oszamruk = []
    ruk = []
    dev = []
    quality_control = []
    zamglt = []
    glt = []

    for user_id, level in global_fetch:
        if level == 1: bloggers.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 8: tech_specs.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == GLOBAL_LEVEL_TECH_CURATOR: tech_curators.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 2: zamruk.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 4: oszamruk.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 6: ruk.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 7: dev.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == GLOBAL_LEVEL_QUALITY_CONTROL: quality_control.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 3: zamglt.append(format_vk_link(user_id, await get_user_name(user_id, None)))
        elif level == 5: glt.append(format_vk_link(user_id, await get_user_name(user_id, None)))

    result = {
        'moders': moders,
        'stmoders': stmoders,
        'admins': admins,
        'stadmins': stadmins,
        'zamspecadm': zamspecadm,
        'specadm': specadm,
        'testers': testers,
        'bloggers': bloggers,
        'tech_specs': tech_specs,
        'tech_curators': tech_curators,
        'zamruk': zamruk,
        'oszamruk': oszamruk,
        'ruk': ruk,
        'dev': dev,
        'quality_control': quality_control,
        'zamglt': zamglt,
        'glt': glt,
    }

    
    if await _is_modified_chat(chat_id):
        custom_roles_fetch = await db_execute(
            f"SELECT level, name FROM custom_roles_{chat_id}", fetch='all'
        )
        role_map = {int(row[0]): row[1] for row in (custom_roles_fetch or [])}
        role_map.setdefault(100, "Владелец беседы")

        await db_execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
        perm_fetch = await db_execute(f"SELECT user_id, level FROM permissions_{chat_id}", fetch='all')

        groups = {}
        owner_ids = []
        primary_owner = await _get_primary_chat_owner_id(chat_id)
        if primary_owner:
            owner_ids.append(primary_owner)
        coowners = await db_execute(
            "SELECT user_id FROM chat_coowners WHERE chat_id = ? ORDER BY created_at, user_id",
            (chat_id,), fetch='all'
        )
        for row in (coowners or []):
            coowner_id = int(row[0])
            if coowner_id not in owner_ids:
                owner_ids.append(coowner_id)
        if owner_ids:
            groups[100] = [
                format_vk_link(uid, await get_user_name(uid, chat_id))
                for uid in owner_ids
            ]
        for uid, level in (perm_fetch or []):
            level = int(level or 0)
            if level <= 0 or level > 99:
                continue
            if level not in role_map:
                continue
            groups.setdefault(level, []).append(format_vk_link(uid, await get_user_name(uid, chat_id)))

        
        for lvl in role_map:
            if int(lvl) != 100:
                groups.setdefault(int(lvl), [])
        sorted_levels = sorted((int(x) for x in role_map.keys()), reverse=True)
        result['custom_sorted'] = [
            (role_map.get(lvl, f"Уровень {lvl}"), groups.get(lvl, []))
            for lvl in sorted_levels
        ]

    return result    

async def add_mute(user_id=int, chat_id=int, moder=int, reason=str, mute_time=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO mutes_{chat_id} VALUES (?, ?, ?, ?, ?, ?)", (user_id, moder, reason, time.time(), date_string, mute_time))
    database.commit()

async def add_mutelog(chat_id=int, user_id=int, moder_id=int, reason=str, mute_time=int, status=str):
    sql.execute(f"CREATE TABLE IF NOT EXISTS mutelogs_{chat_id} (user_id BIGINT, moder_id BIGINT, reason TEXT, date BIGINT, date_string TEXT, mute_time BIGINT, status TEXT);")
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO mutelogs_{chat_id} VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, moder_id, reason, time.time(), date_string, mute_time, status))
    database.commit()

async def get_mute(user_id=int, chat_id=int):
    await checkMute(chat_id, user_id)

    fetch = await db_execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,), fetch='one')

    if fetch == None: return False
    else:
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4],
            'time': fetch[5]
        }

async def unmute(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM mutes_{chat_id} WHERE user_id = {user_id}")
    database.commit()

async def mutelist(chat_id=int):
    sql.execute(f"SELECT * FROM mutes_{chat_id}")
    fetch = sql.fetchall()
    mutes = []
    if fetch==None: return False
    else:
        for i in fetch:
            if not await checkMute(chat_id, i[0]):
                do_time = datetime.fromisoformat(i[4]) + timedelta(minutes=i[5])
                mute_time = str(do_time).split('.')[0]
                try:
                    int(i[1])
                    mutes.append(f"@id{i[0]} (Пользователь) | {i[2]} | @id{i[1]} (модератор) | До: {mute_time}")
                except: mutes.append(f"@id{i[0]} (Пользователь) | {i[2]} | Бот | До: {mute_time}")

    return mutes

async def checkMute(chat_id=int, user_id=int):
    fetch = await db_execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,), fetch='one')
    if not fetch == None:
        do_time = datetime.fromisoformat(fetch[4]) + timedelta(minutes=fetch[5])
        if datetime.now() > do_time:
            await _sqlite_write_with_retry(f"DELETE FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
            return True
        else: return False
    return False

async def get_banwords(chat_id=int):
    sql.execute(f"SELECT * FROM banwords_{chat_id}")
    banwords = []
    fetch = sql.fetchall()
    for i in fetch:
        banwords.append(i[0])

    return banwords


async def get_active_banwords(chat_id=int):
    words = set()

    try:
        sql.execute("SELECT word FROM ban_words")
        for row in sql.fetchall():
            if row and row[0]:
                words.add(str(row[0]).lower())
    except Exception:
        pass

    try:
        for word in await get_banwords(chat_id):
            if word:
                words.add(str(word).lower())
    except Exception:
        pass

    return sorted(words)

async def clear(user_id=int, chat_id=int, group_id=int, peer_id=int):
    sql.execute(f"SELECT cmid FROM messages_{chat_id} WHERE user_id = {user_id}")
    fetch = sql.fetchall()
    cmids = []
    gi = 0
    for i in fetch:
        gi = gi + 1
        if gi <= 199:
            cmids.append(i[0])
    try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmids)
    except: pass

    sql.execute(f"DELETE FROM messages_{chat_id} WHERE user_id = {user_id}")
    database.commit()

async def new_message(user_id=int, message_id=int, cmid=int, chat_id=int):
    try:
        cd = str(datetime.now()).split('.')
        date_string = cd[0]
        async with db_lock:
            sql.execute(f"INSERT INTO messages_{chat_id} VALUES (?, ?, ?, ?, ?)", (user_id, time.time(), date_string, message_id, cmid))
            database.commit()
    except sqlite3.OperationalError as e:
        print(f"[new_message] DB error: {e}")
    except Exception as e:
        print(f"[new_message] error: {e}")

async def add_money(user_id, amount):
    balances = load_data(BALANCES_FILE)
    bal = balances.get(str(user_id), get_balance(user_id))
    bal["wallet"] += amount
    balances[str(user_id)] = bal
    save_data(BALANCES_FILE, balances)
    await log_economy(user_id=user_id, target_id=None, amount=amount, log=f"получил(+а) {amount}₽ через промокод")
    return True

async def give_vip(user_id, days):
    balances = load_data(BALANCES_FILE)
    bal = balances.get(str(user_id), get_balance(user_id))

    now = datetime.now()
    if bal.get("vip_until"):
        try:
            until = datetime.fromisoformat(bal["vip_until"])
            if until > now:
                bal["vip_until"] = (until + timedelta(days=days)).isoformat()
            else:
                bal["vip_until"] = (now + timedelta(days=days)).isoformat()
        except:
            bal["vip_until"] = (now + timedelta(days=days)).isoformat()
    else:
        bal["vip_until"] = (now + timedelta(days=days)).isoformat()

    balances[str(user_id)] = bal
    save_data(BALANCES_FILE, balances)
    await log_economy(user_id=user_id, target_id=None, amount=None, log=f"получил(+а) VIP на {days} дней через промокод")
    return True    


def has_active_vip(balance_data: dict) -> bool:
    vip_until = balance_data.get("vip_until")
    if not vip_until:
        return False
    try:
        return datetime.fromisoformat(str(vip_until)) > datetime.now()
    except Exception:
        return False


async def checkban(user_id: int, chat_id: int):
    try:
        sql.execute(f"SELECT * FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
        fetch = sql.fetchone()
        if not fetch:
            return False
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4]
        }
    except:
        return False  
        
async def checkban_all(user_id: int):
    sql.execute("SELECT chat_id, title FROM chats")
    chats_list = sql.fetchall()

    all_bans = []
    count_bans = 0

    i = 1
    for c in chats_list:
        chat_id_check, chat_title = c
        table_name = f"bans_{chat_id_check}"
        try:
            sql.execute(f"SELECT moderator_id, reason, date FROM {table_name} WHERE user_id = ?", (user_id,))
            user_bans = sql.fetchall()
            for ub in user_bans:
                mod_id, reason, date = ub
                all_bans.append(f"{i}) {chat_title} | @id{mod_id} (Модератор) | {reason} | {date} МСК (UTC+3)")
                i += 1
                count_bans += 1
        except:
            continue  

    return count_bans, all_bans        


async def ban(user_id: int, moder: int, chat_id: int, reason: str):
    
    sql.execute(f"SELECT user_id FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()

    
    date_string = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if fetch is None:
        
        sql.execute(
            f"INSERT INTO bans_{chat_id} (user_id, moder, reason, date) VALUES (?, ?, ?, ?)",
            (user_id, moder, reason, date_string)
        )
        database.commit()
    else:
        
        sql.execute(
            f"UPDATE bans_{chat_id} SET moder = ?, reason = ?, date = ? WHERE user_id = ?",
            (moder, reason, date_string, user_id)
        )
        database.commit()
        
async def unban(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM bans_{chat_id} WHERE user_id = {user_id}")
    database.commit()

async def globalrole(user_id: int, level: int):
    """
    Выдаёт или обновляет глобальную роль пользователя в таблице global_managers.

    level:
        0 - удаление роли
        1 - медиа партнер
        2 - заместитель директора
        3 - заместитель главного тестировщика
        4 - основной заместитель директора
        5 - главный тестировщик
        6 - директор бота
        7 - разработчик бота
        8 - технический специалист
        9 - куратор технических специалистов
        10 - контроль качества
    """
    
    sql.execute("SELECT user_id FROM global_managers WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()

    if fetch is None:
        if level != 0:
            sql.execute("INSERT INTO global_managers (user_id, level) VALUES (?, ?)", (user_id, level))
    else:
        if level == 0:
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (user_id,))
        else:
            sql.execute("UPDATE global_managers SET level = ? WHERE user_id = ?", (level, user_id))

    database.commit()    

async def roleG(user_id=int, chat_id=int, role=int):
    sql.execute(f"SElECT user_id FROM permissions_{chat_id} WHERE user_id = {user_id}")
    fetch = sql.fetchone()
    if fetch == None:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = {user_id}")
        else: sql.execute(f"INSERT INTO permissions_{chat_id} VALUES (?, ?)", (user_id, role))
    else:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = {user_id}")
        else: sql.execute(f"UPDATE permissions_{chat_id} SET level = ? WHERE user_id = ?", (role, user_id))

    database.commit()

async def banlist(chat_id=int):
    sql.execute(f"SELECT * FROM bans_{chat_id}")
    fetch = sql.fetchall()
    banlist = []
    for i in fetch:
        banlist.append(f"@id{i[0]} (Пользователь) | {i[2]} | @id{i[1]} (Модератор) | {i[4]}")

    return banlist

async def quiet(chat_id=int):
    sql.execute(f"SELECT silence FROM chats WHERE chat_id = {chat_id}")
    result = sql.fetchone()[0]
    if not await get_logic(result):
        sql.execute(f"UPDATE chats SET silence = 1 WHERE chat_id = {chat_id}")
        database.commit()
        return True
    else:
        sql.execute(f"UPDATE chats SET silence = 0 WHERE chat_id = {chat_id}")
        database.commit()
        return False

async def get_pull_chats(chat_id=int):
    sql.execute(f"SELECT owner_id, in_pull FROM chats WHERE chat_id = {chat_id}")
    fetch = sql.fetchone()
    if fetch == None: return False
    if not await get_logic(fetch[1]): return False
    sql.execute(f"SELECT chat_id FROM chats WHERE owner_id = ? AND in_pull = ?", (fetch[0], fetch[1]))
    result = []
    fetch2 = sql.fetchall()
    for i in fetch2:
        result.append(i[0])

    return result

async def get_pull_id(chat_id=int):
    sql.execute(f"SELECT in_pull FROM chats WHERE chat_id = {chat_id}")
    fetch = sql.fetchone()
    return fetch[0]

async def rnickall(chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id}")
    database.commit()    

async def banwords(slovo=str, delete=bool, chat_id=int):
    if delete:
        sql.execute(f"DELETE FROM banwords_{chat_id} WHERE banword = ?", (slovo, ))
        database.commit()
    else:
        sql.execute(f"SELECT * FROM banwords_{chat_id} WHERE banword = ?", (slovo, ))
        fetch = sql.fetchone()
        if fetch == None:
            sql.execute(f"INSERT INTO banwords_{chat_id} VALUES (?)", (slovo,))
            database.commit()

async def get_filter(chat_id=int):
    fetch = await db_execute("SELECT filter FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    if not fetch:
        return False
    return await get_logic(fetch[0])

async def set_filter(chat_id=int, value=int):
    sql.execute("UPDATE chats SET filter = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_antiflood(chat_id=int):
    fetch = await db_execute("SELECT antiflood FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    if not fetch:
        return False
    return await get_logic(fetch[0])

async def set_antiflood(chat_id=int, value=int):
    sql.execute("UPDATE chats SET antiflood = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_spam(user_id=int, chat_id=int):
    fetch = await db_execute(
        f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ? ORDER BY date_string DESC LIMIT 3",
        (user_id,),
        fetch='all',
    ) or []
    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))
    if len(list_messages) < 3:
        return False
    list_messages = list_messages[:3]
    return list_messages[0] - list_messages[2] < timedelta(seconds=2)

async def set_welcome(chat_id=int, text=int):
    sql.execute(f"UPDATE chats SET welcome_text = ? WHERE chat_id = ?", (text, chat_id))
    database.commit()

async def get_welcome(chat_id=int):
    fetch = await db_execute("SELECT welcome_text FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    if not fetch or fetch[0] is None:
        return False
    welcome_text = str(fetch[0]).strip()
    if welcome_text.lower() in {"off", "none", ""}:
        return False
    return welcome_text

async def invite_kick(chat_id=int, change=None):
    sql.execute("SELECT invite_kick FROM chats WHERE chat_id = ?", (chat_id, ))
    fetch = sql.fetchone()
    if fetch is None:
        return False
    if not change == None:
        if await get_logic(fetch[0]):
            sql.execute("UPDATE chats SET invite_kick = 0 WHERE chat_id = ?", (chat_id, ))
            database.commit()
            return False
        else:
            sql.execute("UPDATE chats SET invite_kick = 1 WHERE chat_id = ?", (chat_id,))
            database.commit()
            return True
    else:
        return await get_logic(fetch[0])

async def leave_kick(chat_id=int, change=None):
    sql.execute("SELECT leave_kick FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    if change == None: return await get_logic(fetch[0])
    if await get_logic(fetch[0]):
        sql.execute("UPDATE chats SET leave_kick = 0 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return False
    else:
        sql.execute("UPDATE chats SET leave_kick = 1 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return True

def _get_server_link_row(chat_id: int):
    try:
        sql.execute(
            "SELECT owner_id, server_number FROM server_links WHERE chat_id = ? AND owner_id IS NOT NULL AND server_number IS NOT NULL ORDER BY rowid DESC LIMIT 1",
            (int(chat_id),),
        )
        row = sql.fetchone()
        if row:
            return int(row[0]), str(row[1])
    except Exception as error:
        print(f"[_get_server_link_row] chat_id={chat_id} error: {error}")
    return None


async def get_server_chats(chat_id):
    """
    Определяет, к какому серверу принадлежит чат, и возвращает список всех chat_id из этого сервера.
    """
    link_row = _get_server_link_row(chat_id)
    if link_row:
        owner_id, server_number = link_row
        try:
            sql.execute(
                "SELECT chat_id FROM server_links WHERE owner_id = ? AND server_number = ? ORDER BY chat_id",
                (owner_id, server_number),
            )
            chats = [int(row[0]) for row in sql.fetchall()]
            if chats:
                return chats
        except Exception as error:
            print(f"[get_server_chats] server_links error: {error}")

    sql.execute("SELECT owner_id, server_number, table_name FROM servers_list")
    servers = sql.fetchall()

    for owner_id, server_number, table_name in servers:
        try:
            sql.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,))
            if not sql.fetchone():
                continue
            sql.execute(f"SELECT chat_id FROM {table_name} WHERE chat_id = ?", (chat_id,))
            if sql.fetchone():
                sql.execute(f"SELECT chat_id FROM {table_name}")
                chats = sql.fetchall()
                return [c[0] for c in chats]
        except:
            continue
    return None    

async def get_current_server(chat_id):
    """
    Возвращает номер сервера, к которому привязан данный chat_id, или None, если не привязан.
    """
    link_row = _get_server_link_row(chat_id)
    if link_row:
        _owner_id, server_number = link_row
        return server_number

    sql.execute("SELECT owner_id, server_number, table_name FROM servers_list")
    servers = sql.fetchall()

    for owner_id, server_number, table_name in servers:
        try:
            sql.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,))
            if not sql.fetchone():
                continue
            sql.execute(f"SELECT chat_id FROM {table_name} WHERE chat_id = ?", (chat_id,))
            if sql.fetchone():
                return server_number  
        except Exception:
            continue
    return None    

async def message_stats(user_id=int, chat_id=int):
    try:
        async with db_lock:
            sql.execute(f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ?", (user_id, ))
            fetch_all = sql.fetchall()
            count = len(fetch_all)
            if count > 0:
                last = fetch_all[-1][0]
            else:
                last = 0
        return {
            'count': count,
            'last': last
        }
    except Exception as e:
        print(f"[message_stats] error: {e}")
        return {
            'count': 0,
            'last': 0
        }

async def set_pull(chat_id=int, value=int):
    sql.execute(f"UPDATE chats SET in_pull = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_all_peerids():
    sql.execute("SELECT peer_id FROM chats")
    fetch = sql.fetchall()
    peer_ids = []
    for i in fetch:
        peer_ids.append(i[0])

    return peer_ids

async def get_gzov_peerids():
    sql.execute("SELECT peer_id FROM chats WHERE COALESCE(gzov_disabled, 0) = 0")
    fetch = sql.fetchall()
    peer_ids = []
    for i in fetch:
        peer_ids.append(i[0])

    return peer_ids

async def get_gzov_disabled(chat_id: int) -> bool:
    fetch = await db_execute("SELECT COALESCE(gzov_disabled, 0) FROM chats WHERE chat_id = ?", (chat_id,), fetch='one')
    return bool(int(fetch[0])) if fetch and fetch[0] is not None else False

async def set_gzov_disabled(chat_id: int, value: int) -> None:
    sql.execute("UPDATE chats SET gzov_disabled = ? WHERE chat_id = ?", (int(value), int(chat_id)))
    database.commit()

async def add_punishment(chat_id=int, user_id=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO punishments_{chat_id} VALUES (?, ?)", (user_id, date_string))
    database.commit()

async def get_sliv(user_id=int, chat_id=int):
    sql.execute(f"SELECT date FROM punishments_{chat_id}  WHERE user_id = {user_id} ORDER BY date DESC LIMIT 3")
    fetch = sql.fetchall()
    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))
    if len(list_messages) < 3:
        return False
    list_messages = list_messages[:3]
    return list_messages[0] - list_messages[2] < timedelta(seconds=6)

async def get_ServerChat(chat_id: int):
    try:
        link_row = _get_server_link_row(chat_id)
        if not link_row:
            return None
        owner_id, server_number = link_row
        sql.execute(
            "SELECT chat_id FROM server_links WHERE owner_id = ? AND server_number = ? ORDER BY chat_id",
            (owner_id, server_number),
        )
        chats = [int(row[0]) for row in sql.fetchall()]

        return {
            "server": server_number,
            "chats": chats
        }
    except Exception as e:
        print(f"[SERVER] Ошибка при получении сервера: {e}")
        return None     

def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def staff_zov(chat_id=int):
    sql.execute(f"SElECT user_id FROM permissions_{chat_id}")
    fetch = sql.fetchall()
    staff_zov_str = []
    for i in fetch:
        staff_zov_str.append(f"@id{i[0]} (⚜️)")

    return ''.join(staff_zov_str)

async def delete_message(group_id=int, peer_id=int, cmid=int):
    try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
    except: pass

async def get_antisliv(chat_id):
    sql.execute("SELECT mode FROM antisliv WHERE chat_id = ?", (chat_id,))
    data = sql.fetchone()
    return data[0] if data else 0


async def antisliv_mode(chat_id, mode):
    sql.execute("INSERT OR REPLACE INTO antisliv (chat_id, mode) VALUES (?, ?)", (chat_id, mode))
    database.commit()

async def set_onwer(user=int, chat=int):
    sql.execute("UPDATE chats SET owner_id = ? WHERE chat_id = ?", (user, chat))
    database.commit()


def _is_safe_sqlite_identifier(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")))


async def resolve_chat_owner_id(chat_id: int, peer_id: int | None = None) -> int:
    owner_id = 0
    target_peer_id = int(peer_id) if peer_id is not None else 2000000000 + int(chat_id)

    try:
        response = await bot.api.messages.get_conversations_by_id(peer_ids=target_peer_id)
        if response.items:
            live_owner_id = getattr(response.items[0].chat_settings, "owner_id", None)
            if live_owner_id is not None:
                owner_id = int(live_owner_id)
    except Exception as error:
        print(f"[resolve_chat_owner_id] chat_id={chat_id} error: {error}")

    if owner_id <= 0:
        try:
            sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
            row = sql.fetchone()
            owner_id = int(row[0]) if row and row[0] is not None else 0
        except Exception:
            owner_id = 0

    if owner_id > 0:
        try:
            sql.execute("UPDATE chats SET owner_id = ? WHERE chat_id = ?", (owner_id, chat_id))
            database.commit()
        except Exception:
            pass

    return owner_id


def unlink_chat_from_all_server_tables(chat_id: int, keep_table_name: str | None = None) -> None:
    try:
        sql.execute("DELETE FROM server_links WHERE chat_id = ?", (int(chat_id),))
    except Exception as error:
        print(f"[unlink_chat_from_all_server_tables] server_links chat_id={chat_id} error: {error}")

    sql.execute("SELECT owner_id, server_number, table_name FROM servers_list")
    server_rows = sql.fetchall()

    for owner_id, server_number, table_name in server_rows:
        if not _is_safe_sqlite_identifier(table_name):
            continue
        if keep_table_name and table_name == keep_table_name:
            continue
        try:
            sql.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,))
            if not sql.fetchone():
                continue

            sql.execute(f"DELETE FROM {table_name} WHERE chat_id = ?", (chat_id,))
            sql.execute(f"SELECT COUNT(*) FROM {table_name}")
            remaining = int((sql.fetchone() or [0])[0] or 0)
            if remaining <= 0:
                sql.execute(
                    "DELETE FROM servers_list WHERE owner_id = ? AND server_number = ? AND table_name = ?",
                    (owner_id, server_number, table_name),
                )
                sql.execute(f"DROP TABLE IF EXISTS {table_name}")
        except Exception as error:
            print(f"[unlink_chat_from_all_server_tables] chat_id={chat_id} table={table_name} error: {error}")

    database.commit()


async def get_chat_owner_display(chat_id: int) -> str:
    try:
        sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
        row = sql.fetchone()
        owner_id = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        owner_id = 0

    if owner_id != 0:
        return format_vk_link(owner_id, "ADMIN MANAGER")
    return "ADMIN MANAGER"

async def equals_roles(user_id_sender: int, user_id_two: int, chat_id: int, message=None):
    sender_role = await get_role(user_id_sender, chat_id)
    target_role = await get_role(user_id_two, chat_id)
    if _role_hierarchy_value(sender_role) > _role_hierarchy_value(target_role):
        return 2
    return 0
  
chat_types = {
    "def": "общие беседы",
    "ext": "расширенная беседа",
    "pl": "беседа игроков",
    "hel": "беседа хелперов",
    "ld": "беседа лидеров",
    "adm": "беседа администраторов",
    "mod": "беседа модераторов",
    "tex": "беседа техов",
    "test": "беседа тестеров",
    "med": "беседа медиа-партнёров",
    "ruk": "беседа руководства",
    "users": "беседа пользователей"
}

def get_block_game(chat=None):
    try:
        sql.execute("SELECT mode FROM gamesettings WHERE chat_id = ?", (chat,))
        mode_data = sql.fetchone()
        mode = mode_data[0] if mode_data else 0
        if mode == 1:
            return True
        else:
            return False
    except Exception as error:
        print(f"[get_block_game] chat_id={chat} error: {error}")
        try:
            _reset_sql_cursor()
        except Exception:
            pass
        return False


GAME_COMMANDS = set()


MEDIA_COMMANDS = {
    "help", "помощь", "хелп", "команды", "commands",
    "gbanlist", "blacklist",
    "gbanlistminus", "gbanlistplus", "blacklistminus", "blacklistplus",
}


TECH_COMMANDS = {
    "help", "помощь", "хелп", "команды", "commands",
    "логи", "logs", "logsmoders", "логиобщие",
    "млоги", "mlogs", "смлоги", "smlogs",
}

MEDIA_TECH_USER_COMMANDS = {
    "info", "инфо", "информация",
    "rules", "правила", "правилачата",
    "правилабота", "botrules", "rulesbot",
    "infobot", "инфобот", "информациябота",
    "stats", "стата", "статистика", "stata", "statistic",
    "id", "ид", "getid", "гетид", "получитьид", "giveid",
    "q", "выйти",
    "form", "форма",
    "offer", "предложение",
}

MEDIA_ALLOWED_COMMANDS = MEDIA_COMMANDS | MEDIA_TECH_USER_COMMANDS | GAME_COMMANDS
TECH_ALLOWED_COMMANDS = TECH_COMMANDS | MEDIA_TECH_USER_COMMANDS | GAME_COMMANDS


GAME_CALLBACK_COMMANDS = set()


@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def handlers(message: GroupTypes.MessageEvent):
    global balances
    payload = message.object.payload or {}
    command = str(payload.get("command", "")).lower()
    user_id = message.object.user_id
    chat_id = payload.get("chatId")
    if not chat_id:
        peer_id = int(message.object.peer_id)
        chat_id = peer_id - 2000000000 if peer_id >= 2000000000 else 0

    pass

    
    log_cmd = payload.get("log")
    if log_cmd:
        print(f"{user_id} использовал кнопку {command}. ВК выдало: {log_cmd}")
    else:
        print(f"{user_id} использовал кнопку {command}.")
    if command and not await has_required_bot_subscription(user_id):
        await answer_callback_event(message, get_subscription_required_text())
        return True
    if chat_id and get_block_game(chat_id) and command in GAME_CALLBACK_COMMANDS:
        await answer_callback_event(message, "Игровые команды отключены в этой беседе")
        return True

    if command == "gzov_disable":
        target_chat_id = int(chat_id or 0)
        if target_chat_id <= 0:
            await answer_callback_event(message, "Не удалось определить беседу")
            return True
        if not await _can_use_command(user_id, target_chat_id, 7):
            await answer_callback_event(message, "Кнопка доступна только владельцу беседы")
            return True
        if await get_gzov_disabled(target_chat_id):
            await answer_callback_event(message, "Глобальная рассылка уже отключена")
            return True
        await set_gzov_disabled(target_chat_id, 1)
        await answer_callback_event(message, "Глобальная рассылка отключена")
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            random_id=0,
            message="Владелец отключил глобальную рассылку в данный чат.",
            disable_mentions=1,
        )
        return True

    if command == "nicksminus":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(groupid, message.object.peer_id, message.object.conversation_message_id)
        nicks_str = '\n'.join(await nlist(chat_id, page-1))
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи с ником [{page-1} страница]:\n{nicks_str}\n\nПользователи без ников: «/nonick»", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nicksplus":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        nicks = await nlist(chat_id, page + 1)
        if len(nicks) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        nicks_str = '\n'.join(nicks)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,message=f"Пользователи с ником [{page + 1} страница]:\n{nicks_str}\n\nПользователи без ников: «/nonick»",disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "chatsminus":
        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        per_page = 20
        all_rows, _inactive_rows = await get_active_registered_chats()

        new_page = page - 1
        chats_text, max_page = await build_chats_registry_page(all_rows, new_page, per_page)

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "chatsMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "chatsPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список зарегистрированных чатов [{new_page}/{max_page} страница]:\n\n{chats_text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True


    if command == "chatsplus":
        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        per_page = 20
        all_rows, _inactive_rows = await get_active_registered_chats()
        max_page = max(1, (len(all_rows) + per_page - 1) // per_page)

        new_page = page + 1
        if new_page > max_page:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True
        chats_text, max_page = await build_chats_registry_page(all_rows, new_page, per_page)
        if chats_text == "Беседы отсутствуют!":
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "chatsMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "chatsPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список зарегистрированных чатов [{new_page}/{max_page} страница]:\n\n{chats_text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True
        
    if command == "nonicks":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        nonicks = await nonick(chat_id, 1)
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, random_id=0 ,keyboard=keyboard)

    if command == "nicks":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        nicks = await nlist(chat_id, 1)
        nick_list = '\n'.join(nicks)
        if nick_list == "": nick_list = "Ники отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: «/nonick»",
                            disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "nonickminus":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        nonicks = await nonick(chat_id, 1)
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи без ников [{page-1}]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nonickplus":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        page = payload.get("page")
        nonicks = await nonick(chat_id, page+1)
        if len(nonicks) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        nonicks_str = '\n'.join(nonicks)
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"Пользователи без ников [{page + 1}]:\n{nonicks_str}\n\nПользователи с никами: «/nlist»",
                                    disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "clear":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        await clear(user, chat_id, message.group_id, 2000000000+chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000+chat_id, conversation_message_ids=message.object.conversation_message_id, group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) сообщения", disable_mentions=1, random_id=0)

    if command == "unwarn":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id, message) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Вы не можете снять пред данному пользователю!"})
            )
            return True

        await unwarn(chat_id, user)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,conversation_message_ids=message.object.conversation_message_id,group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) предупреждение @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1, random_id=0)

    if command == 'stats':
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        reg_data = await get_registration_date(user)
        info = await bot.api.users.get(user)
        role = await get_role(user, chat_id)
        warns = await get_warns(user, chat_id)
        if await is_nick(user_id, chat_id):
            nick = await get_user_name(user, chat_id)
        else:
            nick = "Нет"
        messages = await message_stats(user_id, chat_id)

        role_display = await get_role_display_name(user, chat_id)

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}), статистика @id{user} (пользователя):\nИмя и фамилия: {info[0].first_name} {info[0].last_name}\nДата регистрации: {reg_data}\nНик: {nick}\nРоль: {role_display}\nВсего предупреждений: {warns}/3\nВсего сообщений: {messages['count']}\nПоследнее сообщение: {messages['last']}", disable_mentions=1, random_id=0)

    if command == "activewarns":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        warns = await gwarn(user, chat_id)
        string_info = str
        if not warns: string_info = "Активных предупреждений нет!"
        else: string_info = f"@id{warns['moder']} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("История всех предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}),
                 color=KeyboardButtonColor.PRIMARY)
        )

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}), информация о активных предупреждениях @id{user} (пользователя):\n{string_info}", disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "warnhistory":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")

        warnhistory_mass = await warnhistory(user, chat_id)
        if not warnhistory_mass:wh_string = "Предупреждений не было!"
        else:wh_string = '\n'.join(warnhistory_mass)

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Информация о всех предупреждениях @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}",disable_mentions=1, random_id=0)

    if command == "unmute":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")

        if await equals_roles(user_id, user, chat_id, None) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        
        mute_info = await get_mute(user, chat_id)
        await unmute(user, chat_id)
        
        if mute_info:
            await add_mutelog(chat_id, user, user_id, mute_info['reason'], mute_info['time'], "снят")
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) размутил(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    if command == "unban":
        if not await _can_use_command(user_id, chat_id, 2):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id, message) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете снять бан данному пользователю!"})
            )
            return True

        await unban(user, chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    if command == "kick":
        if not await _can_use_command(user_id, chat_id, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id, message) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете кикнуть данного пользователя!"})
            )
            return True

        try: await bot.api.messages.remove_chat_user(chat_id, user)
        except: pass

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.model_dump_json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    
    if command in ("astats_day", "astats_week", "astats_all"):
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("user", 0) or 0)
        if not target_user_id:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Ошибка данных!"})
            )
            return True

        await answer_callback_event(message, "Загружаю...")

        try:
            t_info = await bot.api.users.get(user_ids=target_user_id)
            t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
        except:
            t_name = str(target_user_id)

        total_ep = await get_total_epoints(target_user_id)

        if command == "astats_day":
            stat_date = payload.get("date", msk_now().strftime("%Y-%m-%d"))
            target_role = _effective_standard_role_level(await get_role(target_user_id, chat_id))
            norm_status, epoints_reward = await calculate_and_set_norm(target_user_id, target_role, stat_date)
            stat = await _load_admin_stat_day(target_user_id, stat_date)

            status_map = {
                'completed': '✅ Выполнена',
                'stretched': '⚠️ Натянута',
                'not_completed': '❌ Норма не выполнена',
                'no_norm': '❌ Нет нормы'
            }
            status_text = status_map.get(norm_status, norm_status)

            date_fmt = stat_date.split('-')
            date_display = f"{date_fmt[2]}.{date_fmt[1]}.{date_fmt[0]}" if len(date_fmt) == 3 else stat_date

            kb = Keyboard(inline=True)
            kb.add(Callback("Назад", {"command": "astats_back", "user": target_user_id, "owner_id": user_id}))

            detail = _build_admin_stats_detail_text(
                target_user_id,
                t_name,
                total_ep,
                date_display,
                stat,
                target_role,
                status_text,
                epoints_reward,
            )

            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    message=detail,
                    conversation_message_id=message.object.conversation_message_id,
                    keyboard=kb,
                    disable_mentions=1
                )
            except:
                pass

        elif command == "astats_week":
            kb = Keyboard(inline=True)
            kb.add(Callback("Назад", {"command": "astats_back", "user": target_user_id, "owner_id": user_id}))

            week_start = msk_now().date() - timedelta(days=msk_now().date().weekday())
            week_end = week_start + timedelta(days=6)
            week_data = await get_admin_stat_week(target_user_id, week_start)
            week_data = await _merge_admin_norm_totals(
                target_user_id,
                week_data,
                week_start.isoformat(),
                week_end.isoformat(),
            )
            target_role = _effective_standard_role_level(await get_role(target_user_id, chat_id))
            metrics_text = _format_admin_stats_totals(week_data, target_role, "Epoints за неделю")
            detail = (
                f"📊 Статистика за неделю ({week_start.isoformat()})\n\n"
                f"👤 [id{target_user_id}|{t_name}]\n"
                f"Epoints: {format_number(total_ep)} баллов\n\n"
                f"{metrics_text}"
            )
            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    message=detail,
                    conversation_message_id=message.object.conversation_message_id,
                    keyboard=kb,
                    disable_mentions=1
                )
            except:
                pass

        elif command == "astats_all":
            all_data = await get_admin_stat_all_time(target_user_id)
            all_data = await _merge_admin_norm_totals(target_user_id, all_data)
            target_role = _effective_standard_role_level(await get_role(target_user_id, chat_id))
            metrics_text = _format_admin_stats_totals(all_data, target_role, "Всего Epoints")

            kb = Keyboard(inline=True)
            kb.add(Callback("Назад", {"command": "astats_back", "user": target_user_id, "owner_id": user_id}))

            detail = (
                f"📊 Статистика за всё время\n\n"
                f"👤 [id{target_user_id}|{t_name}]\n"
                f"Epoints: {format_number(total_ep)} баллов\n\n"
                f"{metrics_text}"
            )
            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    message=detail,
                    conversation_message_id=message.object.conversation_message_id,
                    keyboard=kb,
                    disable_mentions=1
                )
            except:
                pass

        return True

    
    if command == "astats_page":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("user", 0) or 0)
        page = int(payload.get("page", 0) or 0)
        if not target_user_id or page < 0 or page > 2:
            return True

        await answer_callback_event(message, "Загружаю...")

        try:
            t_info = await bot.api.users.get(user_ids=target_user_id)
            t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
        except:
            t_name = str(target_user_id)

        total_ep = await get_total_epoints(target_user_id)

        target_role_level = _effective_standard_role_level(await get_role(target_user_id, chat_id))
        stats_days = await _load_admin_stats_days(target_user_id, 7)

        start = page * 3
        end = min(start + 3, 7)
        page_days = stats_days[start:end]

        days_text = "".join(_format_admin_stats_day_line(s, target_role_level) for s in page_days)

        prev_page = 2 if page == 0 else page - 1
        next_page = 0 if page == 2 else page + 1

        kb = Keyboard(inline=True)
        for s in page_days:
            kb.add(Callback(s['stat_date'], {"command": "astats_day", "user": target_user_id, "date": s['stat_date'], "owner_id": user_id}))
            kb.row()
        kb.add(Callback("Статистика за неделю", {"command": "astats_week", "user": target_user_id, "owner_id": user_id}))
        kb.row()
        kb.add(Callback("Статистика за всё время", {"command": "astats_all", "user": target_user_id, "owner_id": user_id}))
        kb.row()
        if page == 0:
            kb.add(Callback("Вперед ▶️", {"command": "astats_page", "user": target_user_id, "page": 1, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)
        elif page == 2:
            kb.add(Callback("◀️ Назад", {"command": "astats_page", "user": target_user_id, "page": 1, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)
        else:
            kb.add(Callback("◀️ Назад", {"command": "astats_page", "user": target_user_id, "page": 0, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)
            kb.add(Callback("Вперед ▶️", {"command": "astats_page", "user": target_user_id, "page": 2, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)

        msg = (
            f"📊 Статистика - [id{target_user_id}|{t_name}]\n"
            f"Epoints - {format_number(total_ep)} баллов\n\n"
            f"{days_text.rstrip()}"
        )
        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=msg,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    
    if command == "astats_back":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("user", 0) or 0)
        if not target_user_id:
            return True

        await answer_callback_event(message, "Загружаю...")

        try:
            t_info = await bot.api.users.get(user_ids=target_user_id)
            t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
        except:
            t_name = str(target_user_id)

        total_ep = await get_total_epoints(target_user_id)

        target_role_level = _effective_standard_role_level(await get_role(target_user_id, chat_id))
        stats_days = await _load_admin_stats_days(target_user_id, 7)

        page = 0
        start = page * 3
        end = min(start + 3, 7)
        page_days = stats_days[start:end]

        days_text = "".join(_format_admin_stats_day_line(s, target_role_level) for s in page_days)

        kb = Keyboard(inline=True)
        for s in page_days:
            kb.add(Callback(s['stat_date'], {"command": "astats_day", "user": target_user_id, "date": s['stat_date'], "owner_id": user_id}))
            kb.row()
        kb.add(Callback("Статистика за неделю", {"command": "astats_week", "user": target_user_id, "owner_id": user_id}))
        kb.row()
        kb.add(Callback("Статистика за всё время", {"command": "astats_all", "user": target_user_id, "owner_id": user_id}))
        kb.row()
        kb.add(Callback("Вперед ▶️", {"command": "astats_page", "user": target_user_id, "page": 1, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)

        msg = (
            f"📊 Статистика - [id{target_user_id}|{t_name}]\n"
            f"Epoints - {format_number(total_ep)} баллов\n\n"
            f"{days_text.rstrip()}"
        )
        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=msg,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    
    if command == "ep_shop":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        cat = payload.get("cat", "")
        user_id_cb = message.object.user_id

        role = await get_role(user_id_cb, chat_id)
        if not _role_can_satisfy_standard_min(role, 9):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        total_ep = await get_total_epoints(user_id_cb)

        kb = Keyboard(inline=True)

        if cat == "money":
            ep_shop_inputs[user_id_cb] = {"cat": "money", "created_at": time.time()}
            shop_text = f"💱 Валюта | Баланс: {format_number(total_ep)} EP\n\n1 EP = 0.01₽\n\nВведите количество EP для обмена:"
            kb.add(Callback("Назад", {"command": "ep_back", "owner_id": user_id_cb}))
        else:
            ep_shop_inputs.pop(user_id_cb, None)
            shop_text = "Неизвестная категория."

        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=shop_text,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    if command == "ep_back":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        user_id_cb = message.object.user_id
        ep_shop_inputs.pop(user_id_cb, None)
        total_ep = await get_total_epoints(user_id_cb)

        ep_text = f"💎 Баланс: {format_number(total_ep)} Epoints\n\nВыберите категорию:"

        kb = Keyboard(inline=True)
        kb.add(Callback("💱 Валюты", {"command": "ep_shop", "cat": "money", "owner_id": user_id_cb}))

        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=ep_text,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    if command == "ep_buy":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        user_id_cb = message.object.user_id
        ep_shop_inputs.pop(user_id_cb, None)
        role = await get_role(user_id_cb, chat_id)
        if not _role_can_satisfy_standard_min(role, 9):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        item = payload.get("item", "")
        total_ep = await get_total_epoints(user_id_cb)

        if item in EP_CASE_ITEMS:
            item_def = EP_CASE_ITEMS[item]
            unit_cost = int(item_def["cost"])
            quantity = _ep_payload_quantity(payload, unit_cost, total_ep)
            cost = unit_cost * quantity
            if total_ep < cost:
                await answer_callback_event(message, f"Недостаточно Epoints! Нужно {format_number(cost)}, у вас {format_number(total_ep)}")
                return True
            spent = await spend_epoints(user_id_cb, cost, reason=f"ep_shop:case:{item_def['case_type']}x{quantity}")
            if not spent:
                await answer_callback_event(message, "Ошибка списания Epoints!")
                return True
            case_type = str(item_def["case_type"])
            case_ids = await add_user_cases_bulk(user_id_cb, case_type, quantity)
            await log_economy(
                user_id=user_id_cb,
                target_id=None,
                amount=None,
                log=f"купил(-а) {quantity} кейс(ов) «{CASE_DEFS[case_type]['name']}» за {cost} EP",
            )
            await answer_callback_event(message, f"Куплено кейсов: {quantity}")
            await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
            remaining_ep = await get_total_epoints(user_id_cb)
            await send_case_purchase_result(
                message.object.peer_id,
                user_id_cb,
                case_type,
                quantity,
                case_ids,
                footer=f"💎 Баланс: {format_number(remaining_ep)} Epoints",
            )
            return True

        if item in EP_BUSINESS_ITEMS:
            item_def = EP_BUSINESS_ITEMS[item]
            cost = int(item_def["cost"])
            if total_ep < cost:
                await answer_callback_event(message, f"Недостаточно Epoints! Нужно {format_number(cost)}, у вас {format_number(total_ep)}")
                return True
            spent = await spend_epoints(user_id_cb, cost, reason=f"ep_shop:business:{item_def['business_key']}")
            if not spent:
                await answer_callback_event(message, "Ошибка списания Epoints!")
                return True
            branch_no = await add_business(user_id_cb, str(item_def["business_key"]))
            result_text = f"Куплен бизнес «{item_def['title']}»"
            if branch_no:
                result_text += f" | филиал "
            await log_economy(user_id=user_id_cb, target_id=None, amount=None, log=f"купил(-а) бизнес «{item_def['title']}» за {cost} EP")

        elif item in EP_ITEM_REWARDS:
            item_def = EP_ITEM_REWARDS[item]
            cost = int(item_def["cost"])
            if total_ep < cost:
                await answer_callback_event(message, f"Недостаточно Epoints! Нужно {format_number(cost)}, у вас {format_number(total_ep)}")
                return True
            spent = await spend_epoints(user_id_cb, cost, reason=f"ep_shop:item:{item_def['title']}")
            if not spent:
                await answer_callback_event(message, "Ошибка списания Epoints!")
                return True
            await add_item(user_id_cb, str(item_def["item_type"]), str(item_def["name"]), int(item_def["value"]))
            result_text = f"Получен предмет «{item_def['title']}»"
            await log_economy(user_id=user_id_cb, target_id=None, amount=None, log=f"купил(-а) предмет «{item_def['title']}» за {cost} EP")

        elif item in EP_VIP_ITEMS:
            item_def = EP_VIP_ITEMS[item]
            cost = int(item_def["cost"])
            days = int(item_def["days"])
            if total_ep < cost:
                await answer_callback_event(message, f"Недостаточно Epoints! Нужно {format_number(cost)}, у вас {format_number(total_ep)}")
                return True
            spent = await spend_epoints(user_id_cb, cost, reason=f"ep_shop:vip:{days}d")
            if not spent:
                await answer_callback_event(message, "Ошибка списания Epoints!")
                return True
            await give_vip(user_id_cb, days)
            result_text = f"Выдан VIP на {days} дней"
            await log_economy(user_id=user_id_cb, target_id=None, amount=None, log=f"купил(-а) VIP на {days} дней за {cost} EP")

        else:
            await answer_callback_event(message, "Неизвестный товар")
            return True

        await answer_callback_event(message, f"✅ {result_text} (-{cost} EP)")

        total_ep = await get_total_epoints(user_id_cb)
        kb = Keyboard(inline=True)
        kb.add(Callback("Назад", {"command": "ep_back", "owner_id": user_id_cb}))
        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=f"{result_text}\n\n💎 Баланс: {format_number(total_ep)} Epoints",
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    
    if command == "ep_buy_admin_again":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        user_id_cb = message.object.user_id
        role = await get_role(user_id_cb, chat_id)
        if not _role_can_satisfy_standard_min(role, 9):
            await answer_callback_event(message, "Недостаточно прав!")
            return True
        cost = 800
        total_ep = await get_total_epoints(user_id_cb)
        if total_ep < cost:
            await answer_callback_event(message, f"У вас недостаточно Epoints! Нужно {format_number(cost)}, у вас {format_number(total_ep)}")
            return True

        spent = await spend_epoints(user_id_cb, cost, reason="ep_shop:case:adminx1")
        if not spent:
            await answer_callback_event(message, "Ошибка списания Epoints!")
            return True

        case_ids = await add_user_cases_bulk(user_id_cb, "admin", 1)
        await log_economy(user_id=user_id_cb, target_id=None, amount=None, log="купил(-а) 1 кейс(ов) «Админ кейс» за 800 EP")
        await answer_callback_event(message, "✅ Админ кейс куплен! (-800 EP)")
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        total_ep = await get_total_epoints(user_id_cb)
        await send_case_purchase_result(
            message.object.peer_id,
            user_id_cb,
            "admin",
            1,
            case_ids,
            footer=f"💎 Баланс: {format_number(total_ep)} Epoints",
        )
        return True

    
    if command == 'aфтех_select':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("target", 0))
        target_user_name = payload.get("target_name", "")
        ptype = payload.get("ptype", "")

        if not target_user_id or not ptype:
            await answer_callback_event(message, "Ошибка данных!")
            return True

        await answer_callback_event(message, "Выбрано")

        current_value = ""
        try:
            bal = _cached_user_balance(target_user_id)
            if ptype == "balance":
                current_value = f"\n💰 Текущий баланс: {format_number(int(bal.get('wallet', 0)))}₽"
            elif ptype == "bank":
                current_value = f"\n🏦 Текущий банк: {format_number(int(bal.get('bank', 0)))}₽"
            elif ptype == "vip":
                vip_until = bal.get("vip_until")
                if vip_until:
                    try:
                        vip_dt = datetime.fromisoformat(str(vip_until))
                        if vip_dt > datetime.now():
                            days_left = (vip_dt - datetime.now()).days
                            current_value = f"\n⭐ VIP: активен ({days_left} дн.)"
                        else:
                            current_value = "\n⭐ VIP: неактивен"
                    except:
                        current_value = "\n⭐ VIP: неизвестно"
                else:
                    current_value = "\n⭐ VIP: неактивен"
            elif ptype == "bananas":
                current_value = f"\n🍌 Коины: {format_number(int(bal.get('bananas', 0)))}"
            elif ptype == "rebirth":
                current_value = f"\n🔄 Ребитх: {format_number(int(bal.get('rebirth', 0)))}"
            elif ptype == "business":
                businesses = await get_user_businesses(target_user_id)
                biz_count = len(businesses) if businesses else 0
                total_biz_value = sum(int(b.get("value", 0) or 0) for b in (businesses or []))
                current_value = f"\n🏢 Бизнесов: {biz_count} (на {format_number(total_biz_value)}₽)"
            elif ptype == "cases":
                cases = await get_user_cases(target_user_id)
                current_value = f"\n📦 Кейсов: {len(cases) if cases else 0}"
            elif ptype == "inventory":
                inv = await get_inventory(target_user_id)
                current_value = f"\n🎒 Предметов в инвентаре: {len(inv) if inv else 0}"
            elif ptype == "items":
                inv = await get_inventory(target_user_id)
                current_value = f"\n🎁 Предметов: {len(inv) if inv else 0}"
        except Exception as e:
            print(f"[AF_TECH] Ошибка получения данных: {e}")

        kb = Keyboard(inline=True)
        if ptype in ("business", "cases", "bananas", "rebirth", "balance", "bank", "vip", "inventory", "items"):
            kb.add(Callback("✅ Продолжить", {"command": "aфtech_proceed", "target": target_user_id, "target_name": target_user_name, "ptype": ptype, "owner_id": user_id}))
        kb.row()
        kb.add(Callback("⬅️ Назад", {"command": "aфтех_back", "target": target_user_id, "target_name": target_user_name, "owner_id": user_id}))

        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=(
                f"📋 Заявка на обнуление\n\n"
                f"👤 Цель: [id{target_user_id}|{target_user_name}]\n"
                f"📦 Раздел: {get_property_type_name(ptype)}"
                f"{current_value}\n\n"
                f"Нажмите «Продолжить» для перехода к доказательству:"
            ),
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    
    if command == 'aфtech_proceed':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("target", 0))
        target_user_name = payload.get("target_name", "")
        ptype = payload.get("ptype", "")
        if not target_user_id or not ptype:
            await answer_callback_event(message, "Ошибка данных!")
            return True
        await answer_callback_event(message, "Загрузите фото")

        property_exchange_drafts[int(user_id)] = {
            "mode": "reset_request",
            "target_user_id": target_user_id,
            "target_user_name": target_user_name,
            "property_type": ptype,
            "peer_id": message.object.peer_id,
            "cmid": message.object.conversation_message_id,
            "awaiting_proof": True,
            "awaiting_reason": False,
        }

        type_name = "ВСЁ имущество" if ptype == "all" else get_property_type_name(ptype)
        kb = Keyboard(inline=True)
        kb.add(Callback("❌ Отменить", {"command": "aфtech_cancel", "owner_id": user_id}))

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message=(
                    f"📋 Заявка на обнуление\n\n"
                    f"👤 Цель: [id{target_user_id}|{target_user_name}]\n"
                    f"📦 Раздел: {type_name}\n\n"
                    f"📎 Шаг 1: Отправьте фото или видео как доказательство."
                ),
                conversation_message_id=message.object.conversation_message_id,
                keyboard=kb,
                disable_mentions=1
            )
        except:
            pass
        return True

    
    if command == 'aфтех_back':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("target", 0))
        target_user_name = payload.get("target_name", "")
        if not target_user_id:
            await answer_callback_event(message, "Ошибка данных!")
            return True
        await answer_callback_event(message, "Назад")

        kb = Keyboard(inline=True)
        for i, (pt, pname) in enumerate(RESET_PROPERTY_TYPES):
            kb.add(Callback(pname, {"command": "aфтех_select", "target": target_user_id, "target_name": target_user_name, "ptype": pt, "owner_id": user_id}))
            if (i + 1) % 3 == 0:
                kb.row()
        kb.row()
        kb.add(Callback("🔄 Обнулить всё", {"command": "aфtech_proceed", "target": target_user_id, "target_name": target_user_name, "ptype": "all", "owner_id": user_id}))

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message=(
                    f"📋 Заявка на обнуление/восстановление\n\n"
                    f"👤 Цель: [id{target_user_id}|{target_user_name}]\n\n"
                    f"Выберите тип имущества:"
                ),
                conversation_message_id=message.object.conversation_message_id,
                keyboard=kb,
                disable_mentions=1
            )
        except:
            pass
        return True

    
    if command == 'aфtech_confirm':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True

        draft = property_exchange_drafts.get(int(user_id))
        if not draft or draft.get("mode") != "reset_request":
            await answer_callback_event(message, "Заявка устарела!")
            return True

        target_user_id = int(payload.get("target", 0))
        target_user_name = payload.get("target_name", "")
        ptype = payload.get("ptype", "")
        proof_photo = draft.get("proof_photo", "")
        reason = draft.get("reason", "Не указана")

        try:
            user_info = await bot.api.users.get(user_ids=user_id)
            mod_name = f"{user_info[0].first_name} {user_info[0].last_name}"
        except:
            mod_name = str(user_id)

        request_id = await create_reset_request(
            target_user_id=target_user_id,
            target_user_name=target_user_name,
            moderator_id=user_id,
            moderator_name=mod_name,
            property_type=ptype,
            proof_photo=proof_photo,
            reason=reason,
        )

        property_exchange_drafts.pop(int(user_id), None)

        type_name = "ВСЁ имущество" if ptype == "all" else get_property_type_name(ptype)
        status_msg = (
            f"📋 Заявка на обнуление "
            f"👤 Цель: [id{target_user_id}|{target_user_name}]\n"
            f"📦 Раздел: {type_name}\n"
            f"📝 Причина: {reason}\n"
            f"📎 Доказательство: {'✅' if proof_photo else '❌'}\n\n"
            f"⏳ Заявка отправлена на рассмотрение."
        )

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message=status_msg,
                conversation_message_id=message.object.conversation_message_id,
                keyboard=None,
                disable_mentions=1
            )
        except:
            pass

        try:
            sql.execute("SELECT chat_id FROM af_tech_config WHERE id = 1")
            af_row = sql.fetchone()
            if af_row:
                af_chat_id = af_row[0]
                target_link = f"[id{target_user_id}|{target_user_name}]" if target_user_id > 0 else "Не указан"
                form_msg = (
                    f"📋 Заявка на обнуление "
                    f"Отправитель: [id{user_id}|{mod_name}]\n"
                    f"Тип: Обнуление\n"
                    f"Раздел: {type_name}\n"
                    f"Причина: {reason}\n"
                    f"Наказуемый: {target_link}\n\n"
                    f"Ожидайте рассмотрения."
                )
                attach = proof_photo if proof_photo else ""
                if attach:
                    await bot.api.messages.send(peer_id=2000000000 + af_chat_id, message=form_msg, attachment=attach, random_id=0, disable_mentions=1)
                else:
                    await bot.api.messages.send(peer_id=2000000000 + af_chat_id, message=form_msg, random_id=0, disable_mentions=1)
        except Exception as e:
            print(f"[AF_TECH] Ошибка отправки в чат форм: {e}")

        await answer_callback_event(message, f"✅ Заявка ")
        return True

    
    if command == 'aфtech_back_proof':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("target", 0))
        target_user_name = payload.get("target_name", "")
        ptype = payload.get("ptype", "")
        if not target_user_id:
            await answer_callback_event(message, "Ошибка данных!")
            return True
        await answer_callback_event(message, "Загрузите фото")

        draft = property_exchange_drafts.get(int(user_id))
        if draft:
            draft["awaiting_proof"] = True
            draft["awaiting_reason"] = False
            draft.pop("proof_photo", None)
            property_exchange_drafts[int(user_id)] = draft

        type_name = "ВСЁ имущество" if ptype == "all" else get_property_type_name(ptype)
        kb = Keyboard(inline=True)
        kb.add(Callback("❌ Отменить", {"command": "aфtech_cancel", "owner_id": user_id}))

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message=(
                    f"📋 Заявка на обнуление\n\n"
                    f"👤 Цель: [id{target_user_id}|{target_user_name}]\n"
                    f"📦 Раздел: {type_name}\n\n"
                    f"📎 Шаг 1: Отправьте фото или видео как доказательство."
                ),
                conversation_message_id=message.object.conversation_message_id,
                keyboard=kb,
                disable_mentions=1
            )
        except:
            pass
        return True

    
    if command == 'aфtech_cancel':
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True

        property_exchange_drafts.pop(int(user_id), None)

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message="❌ Заявка отменена.",
                conversation_message_id=message.object.conversation_message_id,
                keyboard=None,
                disable_mentions=1
            )
        except:
            pass
        return True

    if command == 'aфtech_accept':
        request_id = payload.get("request_id", "")
        if not request_id:
            await answer_callback_event(message, "Ошибка данных!")
            return True

        role = await get_role(user_id, chat_id)
        if not _role_can_satisfy_standard_min(role, 12):
            await answer_callback_event(message, "Недостаточно прав!")
            return True

        req = await get_reset_request(request_id)
        if not req:
            await answer_callback_event(message, "Заявка не найдена!")
            return True

        target_user_id = int(req['target_user_id'])
        ptype = req.get('property_type', '')

        await _perform_user_reset(target_user_id, ptype)
        await approve_reset_request(request_id, user_id)
        await increment_forms_accepted(user_id)
        await increment_resets_done(user_id)
        await _recalculate_admin_norm_for_user(user_id, chat_id)

        role_level = _effective_standard_role_level(role)
        if _is_encoded_global_role(role) and 12 <= role_level <= 14:
            await add_epoints_to_stat(user_id, 50)

        req = await get_reset_request(request_id)
        if req and req.get('moderator_id'):
            await add_epoints_to_stat(req['moderator_id'], 200)
            await chats_log(user_id=user_id, target_id=req['moderator_id'], role=None, log=f"принял(-а) тех. форму ")

        user_name = await get_user_name(user_id, chat_id)

        try:
            x_resp = await bot.api.messages.get_by_conversation_message_id(
                peer_id=message.object.peer_id,
                conversation_message_ids=message.object.conversation_message_id,
                group_id=message.group_id
            )
            items = json.loads(x_resp.model_dump_json()).get('items', [])
            if items:
                x_text = items[0]['text']
                edit_kwargs = {
                    "peer_id": message.object.peer_id,
                    "message": f"{x_text}\n\n✅ Принято: [id{user_id}|{user_name}]",
                    "conversation_message_id": message.object.conversation_message_id,
                    "keyboard": None,
                }
                proof_attachment = str((req or {}).get("proof_photo") or "").strip()
                if proof_attachment:
                    edit_kwargs["attachment"] = proof_attachment
                try:
                    await bot.api.messages.edit(**edit_kwargs)
                except:
                    edit_kwargs.pop("attachment", None)
                    await bot.api.messages.edit(**edit_kwargs)
        except:
            pass

        await answer_callback_event(message, f"✅ Заявка {request_id} одобрена!")
        return True

    
    if command == 'aфtech_reject':
        request_id = payload.get("request_id", "")
        if not request_id:
            await answer_callback_event(message, "Ошибка данных!")
            return True

        role = await get_role(user_id, chat_id)
        if not _role_can_satisfy_standard_min(role, 12):
            await answer_callback_event(message, "Недостаточно прав!")
            return True

        await reject_reset_request(request_id, user_id, "Отклонено модератором")

        role_level = _effective_standard_role_level(role)
        if _is_encoded_global_role(role) and 12 <= role_level <= 14:
            await add_epoints_to_stat(user_id, 20)

        req = await get_reset_request(request_id)
        if req and req.get('moderator_id'):
            await chats_log(user_id=user_id, target_id=req['moderator_id'], role=None, log=f"отклонил(-а) тех. форму ")

        user_name = await get_user_name(user_id, chat_id)

        try:
            x_resp = await bot.api.messages.get_by_conversation_message_id(
                peer_id=message.object.peer_id,
                conversation_message_ids=message.object.conversation_message_id,
                group_id=message.group_id
            )
            items = json.loads(x_resp.model_dump_json()).get('items', [])
            if items:
                x_text = items[0]['text']
                edit_kwargs = {
                    "peer_id": message.object.peer_id,
                    "message": f"{x_text}\n\n❌ Отклонено: [id{user_id}|{user_name}]",
                    "conversation_message_id": message.object.conversation_message_id,
                    "keyboard": None,
                }
                proof_attachment = str((req or {}).get("proof_photo") or "").strip()
                if proof_attachment:
                    edit_kwargs["attachment"] = proof_attachment
                try:
                    await bot.api.messages.edit(**edit_kwargs)
                except:
                    edit_kwargs.pop("attachment", None)
                    await bot.api.messages.edit(**edit_kwargs)
        except:
            pass

        await answer_callback_event(message, f"❌ Заявка {request_id} отклонена!")
        return True

    
    if command == 'aфtech_exit':
        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                conversation_message_id=message.object.conversation_message_id,
                message="Заявка закрыта.",
                keyboard=None
            )
        except:
            pass
        return True

    
    if command in ['aфтехлист', 'афтехlist', 'aфлист', 'aflist']:
        await answer_callback_event(message, " ")
        role = await get_role(user_id, chat_id)
        if not _role_can_satisfy_standard_min(role, 12):
            return True
        page = int(payload.get("page", 0) or 0)
        pending_requests = await get_pending_reset_requests("pending")
        if not pending_requests:
            await answer_callback_event(message, "Нет активных заявок.")
            return True
        per_page = 5
        total = len(pending_requests)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        page_items = pending_requests[page * per_page:(page + 1) * per_page]
        kb = Keyboard(inline=True)
        for req in page_items:
            tname = str(req.get('target_user_name', '??'))[:15]
            label = f"Обнуление - {tname}"
            if len(label) > 40:
                label = label[:37] + "..."
            kb.add(Callback(label, {"command": "aфtech_view", "request_id": req['request_id']}))
        kb.row()
        nav_parts = []
        if page > 0:
            nav_parts.append(Callback("◀️", {"command": "aфтехлист", "page": page - 1}))
        if total_pages > 1:
            nav_parts.append(Callback(f"{page+1}/{total_pages}", {"command": "aфtechlist_nop"}))
        if page < total_pages - 1:
            nav_parts.append(Callback("▶️", {"command": "aфтехлист", "page": page + 1}))
        for btn in nav_parts:
            kb.add(btn)
        kb.row()
        kb.add(Callback("🔄", {"command": "aфтехлист", "page": 0}))
        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=f"📋 Заявки на обнуление ({total} шт.)\nВыберите заявку:",
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb
        )
        return True

    if command == 'aфtechlist_nop':
        await answer_callback_event(message, " ")
        return True

    
    if command == 'aфtech_view':
        await answer_callback_event(message, " ")
        request_id = payload.get("request_id", "")
        if not request_id:
            await answer_callback_event(message, "Ошибка данных!")
            return True

        req = await get_reset_request(request_id)
        if not req:
            await answer_callback_event(message, "Заявка не найдена!")
            return True

        form_number = req.get('id', '?')
        author_name = await get_user_name(req['moderator_id'], None) if req.get('moderator_id') else "Неизвестно"
        reason_text = req.get('reason', '') or "Не указана"
        proof_attachment = str(req.get('proof_photo') or "").strip()
        has_proof = "✅" if proof_attachment else "❌"

        status_map = {
            'pending': '⏳ Ожидает рассмотрения',
            'approved': '✅ Одобрена',
            'rejected': '❌ Отклонена',
        }
        status_display = status_map.get(req.get('status', 'pending'), req.get('status', 'pending'))

        req_text = (
            f"📋 Тех. форма "
            f"👤 Автор: [id{req['moderator_id']}|{author_name}]\n"
            f"🎯 Цель: [id{req['target_user_id']}|{req['target_user_name']}]\n"
            f"📂 Тип: Обнуление\n"
            f"📦 Раздел: {get_property_type_name(req['property_type'])}\n"
            f"📝 Причина: {reason_text}\n"
            f"📎 Доказательства: {has_proof}\n"
            f"⏳ Статус: {status_display}\n"
            f"🕐 Создана: {req.get('created_at', 'Неизвестно')}\n"
            f"🆔 ID: {req['request_id']}"
        )

        kb = Keyboard(inline=True)
        if req.get('status', 'pending') == 'pending':
            kb.add(Callback("✅ Принять", {"command": "aфtech_accept", "request_id": request_id}))
            kb.add(Callback("❌ Отклонить", {"command": "aфtech_reject", "request_id": request_id}))
        kb.row()
        kb.add(Callback("🚪 Выход", {"command": "aфtech_exit"}))

        edited_with_attachment = False
        edit_kwargs = {
            "peer_id": message.object.peer_id,
            "message": req_text,
            "conversation_message_id": message.object.conversation_message_id,
            "keyboard": kb,
            "disable_mentions": 1,
        }
        if proof_attachment:
            edit_kwargs["attachment"] = proof_attachment
        try:
            await bot.api.messages.edit(**edit_kwargs)
            edited_with_attachment = bool(proof_attachment)
        except Exception as e:
            print(f"[AF_TECH] Ошибка редактирования заявки с доказательством: {e}")
            try:
                edit_kwargs.pop("attachment", None)
                await bot.api.messages.edit(**edit_kwargs)
            except:
                pass

        if proof_attachment and not edited_with_attachment:
            try:
                await bot.api.messages.send(
                    peer_id=message.object.peer_id,
                    attachment=proof_attachment,
                    message=f"📎 Доказательства к тех. форме ",
                    random_id=0,
                    disable_mentions=1
                )
            except Exception as e:
                print(f"[AF_TECH] Ошибка отправки фото доказательства: {e}")

        return True

    
    if command in ("promo_set_limit", "promo_set_custom_limit"):
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        if user_id not in promo_creations:
            return True
        draft = promo_creations[user_id]
        limit = payload.get("limit", 0)

        if command == "promo_set_custom_limit":
            draft["step"] = "custom_limit"
            await answer_callback_event(message, "Введите число")
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Введите количество активаций (число):",
                random_id=0,
                disable_mentions=1
            )
            return True

        draft["max_uses"] = int(limit)
        draft["uses_left"] = int(limit)
        draft["step"] = "type"

        kb = Keyboard(inline=True)
        kb.add(Callback("💰 Деньги", {"command": "promo_set_type", "type": "money"}))
        kb.add(Callback("⭐ VIP", {"command": "promo_set_type", "type": "vip"}))
        kb.row()
        kb.add(Callback("📦 Кейс", {"command": "promo_set_type", "type": "case"}))
        kb.add(Callback("🍌 Коины", {"command": "promo_set_type", "type": "bananas"}))
        kb.row()

        limit_text = "∞" if int(limit) == 0 else str(limit)
        await answer_callback_event(message, f"Лимит: {limit_text}")
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"📝 Промокод: «{draft['code']}»\nЛимит: {limit_text}\n\nВыберите тип награды:",
            keyboard=kb,
            random_id=0,
            disable_mentions=1
        )
        return True

    if command == "promo_set_type":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        if user_id not in promo_creations:
            return True
        draft = promo_creations[user_id]
        ptype = payload.get("type", "")
        draft["type"] = ptype

        if ptype == "money":
            draft["step"] = "value"
            await answer_callback_event(message, "Введите сумму")
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Введите сумму денег (число):",
                random_id=0,
                disable_mentions=1
            )
        elif ptype == "vip":
            draft["step"] = "value"
            await answer_callback_event(message, "Введите дни")
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Введите количество дней VIP:",
                random_id=0,
                disable_mentions=1
            )
        elif ptype == "bananas":
            draft["step"] = "value"
            await answer_callback_event(message, "Введите бананы")
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Введите количество коинов:",
                random_id=0,
                disable_mentions=1
            )
        elif ptype == "case":
            kb = Keyboard(inline=True)
            kb.add(Callback("🆓 Ежедневный", {"command": "promo_set_case", "case_type": "daily", "owner_id": user_id}))
            kb.add(Callback("💸 Бомжа", {"command": "promo_set_case", "case_type": "homeless", "owner_id": user_id}))
            kb.row()
            kb.add(Callback("🎟 Стандартный", {"command": "promo_set_case", "case_type": "standard", "owner_id": user_id}))
            kb.add(Callback("🍌 Особый", {"command": "promo_set_case", "case_type": "special", "owner_id": user_id}))
            kb.row()
            kb.add(Callback("🎖 День Победы", {"command": "promo_set_case", "case_type": "victory_day", "owner_id": user_id}))
            kb.add(Callback("🏖 Летний", {"command": "promo_set_case", "case_type": "summer_mayhem", "owner_id": user_id}))
            kb.add(Callback("🖤 BLACK POINT", {"command": "promo_set_case", "case_type": "black_point", "owner_id": user_id}))

            await answer_callback_event(message, "Выберите кейс")
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Выберите тип кейса:",
                keyboard=kb,
                random_id=0,
                disable_mentions=1
            )
        return True

    if command == "promo_set_case":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        if user_id not in promo_creations:
            return True
        draft = promo_creations[user_id]
        case_type = payload.get("case_type", "")
        draft["value"] = case_type
        draft["step"] = "done"

        sql.execute("SELECT * FROM promocodes WHERE code = ?", (draft["code"],))
        if sql.fetchone():
            await answer_callback_event(message, "Такой промокод уже существует!")
            promo_creations.pop(user_id, None)
            return True

        sql.execute(
            "INSERT INTO promocodes (code, type, value, creator_id, uses_left, max_uses) VALUES (?, ?, ?, ?, ?, ?)",
            (draft["code"], "case", case_type, user_id, draft["uses_left"], draft["max_uses"])
        )
        database.commit()

        limit_text = "∞" if draft["max_uses"] == 0 else str(draft["max_uses"])
        case_name = CASE_DEFS.get(case_type, {}).get("name", case_type)
        await answer_callback_event(message, "Промокод создан!")
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ Промокод создан!\n\nКод: «{draft['code']}»\nТип: case\nКейс: {case_name}\nЛимит: {limit_text}",
            random_id=0,
            disable_mentions=1
        )
        promo_creations.pop(user_id, None)
        return True

    
    if command == "norm_refresh":
        if not _callback_owner_allowed(payload, user_id):
            await answer_callback_event(message, "Эта кнопка доступна только автору команды")
            return True
        target_user_id = int(payload.get("user", 0) or 0)
        role = await get_role(message.object.user_id, chat_id)
        if not _role_can_satisfy_standard_min(role, 9):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        today_str = msk_now().strftime("%Y-%m-%d")
        target_role = _effective_standard_role_level(await get_role(target_user_id, chat_id))
        norm_status, epoints_reward = await calculate_and_set_norm(target_user_id, target_role, today_str)

        status_map = {'completed': '✅ Выполнена', 'stretched': '⚠️ Натянута', 'not_completed': '❌ Норма не выполнена', 'no_norm': '❌ Нет нормы'}
        status_text = status_map.get(norm_status, norm_status)

        try:
            t_info = await bot.api.users.get(user_ids=target_user_id)
            t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
        except:
            t_name = str(target_user_id)

        role_names = {
            9: "Технический специалист",
            10: "Заместитель директора",
            11: "Основной заместитель директора",
            12: "Куратор техспециалистов",
            13: "Зам. Главного Тех. Специалиста",
            14: "Главный Тех. Специалист",
            15: "Директор бота",
            16: GLOBAL_QUALITY_CONTROL_NAME,
            17: "Разработчик бота"
        }
        target_role_name = role_names.get(target_role, f"Роль {target_role}")

        norm_text = f"📋 Норма - [id{target_user_id}|{t_name}] (обновлено)\n"
        norm_text += f"Роль: {target_role_name}\n"
        norm_text += f"Статус: {status_text}\n\n"
        norm_text += await _build_admin_norm_progress_text(target_user_id, target_role, today_str)
        norm_text += "\n\n"

        if target_role == 9:
            norm_text += "Норма Технического специалиста:\n\n"
            norm_text += "✅ Выполнена:\n"
            norm_text += "  • Отправлено форм: ≥2\n"
            norm_text += "  • Сообщений: ≥100\n"
            norm_text += f"  • Награда: 100 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n"
            norm_text += "  • Отправлено форм: ≥1\n"
            norm_text += "  • Сообщений: ≥50\n"
            norm_text += f"  • Награда: 50 Epoints\n"
        elif target_role == 10:
            norm_text += "Норма Заместителя директора:\n\n"
            norm_text += "✅ Выполнена:\n  • Сообщений: ≥600\n"
            norm_text += f"  • Награда: 300 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Сообщений: ≥300\n"
            norm_text += f"  • Награда: 150 Epoints\n"
        elif target_role == 11:
            norm_text += "Норма Основного заместителя директора:\n\n"
            norm_text += "✅ Выполнена:\n  • Сообщений: ≥400\n"
            norm_text += f"  • Награда: 400 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Сообщений: ≥200\n"
            norm_text += f"  • Награда: 200 Epoints\n"
        elif target_role == 12:
            norm_text += "Норма Куратора техспециалистов:\n\n"
            norm_text += "✅ Выполнена:\n  • Принято форм: ≥3\n  • Обнулений/восстановлений: ≥1\n"
            norm_text += f"  • Награда: 150 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Принято форм: ≥1\n"
            norm_text += f"  • Награда: 75 Epoints\n"
        elif target_role == 13:
            norm_text += "Норма Зам. Главного Тех. Специалиста:\n\n"
            norm_text += "✅ Выполнена:\n  • Принято форм: ≥5\n  • Обнулений/восстановлений: ≥2\n"
            norm_text += f"  • Награда: 350 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Принято форм: ≥2\n  • Обнулений/восстановлений: ≥1\n"
            norm_text += f"  • Награда: 175 Epoints\n"
        elif target_role == 14:
            norm_text += "Норма Главного Тех. Специалиста:\n\n"
            norm_text += "✅ Выполнена:\n  • Принято форм: ≥8\n  • Обнулений/восстановлений: ≥3\n"
            norm_text += f"  • Награда: 450 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Принято форм: ≥4\n  • Обнулений/восстановлений: ≥1\n"
            norm_text += f"  • Награда: 225 Epoints\n"
        elif target_role == 15:
            norm_text += "Норма Директора бота:\n\n"
            norm_text += "✅ Выполнена:\n  • Сообщений: ≥200\n"
            norm_text += f"  • Награда: 500 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n  • Сообщений: ≥100\n"
            norm_text += f"  • Награда: 250 Epoints\n"
        elif target_role == 16:
            norm_text += "Норма Контроля качества:\n\n"
            norm_text += "✅ Выполнена:\n"
            norm_text += "  • Сообщений: ≥200\n"
            norm_text += f"  • Награда: 500 Epoints\n\n"
            norm_text += "⚠️ Натянута:\n"
            norm_text += "  • Сообщений: ≥100\n"
            norm_text += f"  • Награда: 250 Epoints\n"
        elif target_role == 17:
            norm_text += "Норма Разработчика бота:\n\n"
            norm_text += "✅ Выполнена (любое действие):\n  • Сообщений: ≥1\n"
            norm_text += f"  • Награда: 500 Epoints\n"

        kb = Keyboard(inline=True)
        kb.add(Callback("🔄 Обновить норму", {"command": "norm_refresh", "user": target_user_id, "owner_id": user_id}))

        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=norm_text,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=kb,
            disable_mentions=1
        )
        return True

    if command == "approve_form" or command == "reject_form":
        
        chat_id = message.object.peer_id
        if chat_id > 2000000000:  
            chat_id -= 2000000000

        
        if not await _can_use_command(user_id, chat_id, 9):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        
        target = payload.get("target")
        sender = payload.get("sender")
        reason = payload.get("reason", "Не указано")

        if not target or not sender:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Ошибка: нет данных пользователя"})
            )
            return True

        
        x_resp = await bot.api.messages.get_by_conversation_message_id(
            peer_id=message.object.peer_id,
            conversation_message_ids=message.object.conversation_message_id,
            group_id=message.group_id
        )
        items = json.loads(x_resp.model_dump_json()).get('items', [])
        if not items:
            return True
        x_text = items[0]['text']

        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            message=x_text,
            conversation_message_id=message.object.conversation_message_id,
            keyboard=None
        )

        
        if command == "approve_form":
            sql.execute(
                "INSERT INTO gbanlist (user_id, moderator_id, reason_gban, datetime_globalban) VALUES (?, ?, ?, ?)",
                (target, user_id, f"{reason} | By form | @id{sender} (пользователь)",
                 datetime.now().strftime("%d.%m.%Y %H:%M"))
            )
            database.commit()

            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) одобрил форму пользователя @id{sender} ({await get_user_name(sender, chat_id)})",
                disable_mentions=1,
                random_id=0
            )
        else:
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) отклонил форму пользователя @id{sender} ({await get_user_name(sender, chat_id)})",
                disable_mentions=1,
                random_id=0
            )

        return True

    if command == "banwordsminus":
        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT word, creator_id, time FROM ban_words ORDER BY time DESC")
        rows = sql.fetchall()
        total = len(rows)
        per_page = 5
        max_page = (total + per_page - 1) // per_page

        async def get_words_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            formatted = []
            for i, (word, creator, tm) in enumerate(rows[start:end], start=start + 1):
                try:
                    info = await bot.api.users.get(user_ids=creator)
                    creator_name = f"{info[0].first_name} {info[0].last_name}"
                except:
                    creator_name = "Не удалось получить имя"
                formatted.append(f"{i}. {word} | @id{creator} ({creator_name}) | Время: {tm}")
            return formatted

        new_page = page - 1
        words = await get_words_page(new_page)
        words_text = "\n\n".join(words)

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "banwordsMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "banwordsPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Запрещённые слова (Страница: {new_page}):\n\n{words_text}\n\nВсего запрещенных слов: {total}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True


    if command == "banwordsplus":
        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        sql.execute("SELECT word, creator_id, time FROM ban_words ORDER BY time DESC")
        rows = sql.fetchall()
        total = len(rows)
        per_page = 5
        max_page = (total + per_page - 1) // per_page

        async def get_words_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            formatted = []
            for i, (word, creator, tm) in enumerate(rows[start:end], start=start + 1):
                try:
                    info = await bot.api.users.get(user_ids=creator)
                    creator_name = f"{info[0].first_name} {info[0].last_name}"
                except:
                    creator_name = "Не удалось получить имя"
                formatted.append(f"{i}. {word} | @id{creator} ({creator_name}) | Время: {tm}")
            return formatted

        new_page = page + 1
        words = await get_words_page(new_page)
        if len(words) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        words_text = "\n\n".join(words)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "banwordsMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "banwordsPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Запрещённые слова (Страница {new_page}):\n\n{words_text}\n\nВсего запрещенных слов: {total}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True        
        
    if command == "join_duel":
        try:
            
            data = {}
            if message.object.payload:
                try:
                    if isinstance(message.object.payload, str):
                        data = json.loads(message.object.payload)
                    elif isinstance(message.object.payload, dict):
                        data = message.object.payload
                    else:
                        print(f"[join_duel] payload неизвестного типа: {type(message.object.payload)}")
                except Exception as e:
                    print(f"[join_duel] Ошибка парсинга payload: {e}")

            peer = str(data.get("peer")) if data else None
            print(f"[join_duel] peer из payload: {peer}")

            if not peer or peer not in duels:
                print(f"[join_duel] Дуэль недоступна: ключ '{peer}' не найден в duels. "
                      f"Текущие ключи: {list(duels.keys())}")
                await bot.api.messages.send_message_event_answer(
                    event_id=message.object.event_id,
                    peer_id=message.object.peer_id,
                    user_id=message.object.user_id,
                    event_data=json.dumps({"type": "show_snackbar", "text": "⚔️ Дуэль недоступна"})
                )
                return True

            duel = duels[peer]
            print(f"[join_duel] Найдена дуэль: {duel}")
            expires_at = duel.get("expires_at")
            if expires_at:
                try:
                    if datetime.now() >= datetime.fromisoformat(expires_at):
                        duels.pop(peer, None)
                        save_data(DUELS_FILE, duels)
                        await bot.api.messages.send_message_event_answer(
                            event_id=message.object.event_id,
                            peer_id=message.object.peer_id,
                            user_id=message.object.user_id,
                            event_data=json.dumps({"type": "show_snackbar", "text": "⚔️ Время дуэли истекло"})
                        )
                        return True
                except Exception:
                    pass
            author = duel["author"]
            stake = duel["stake"]
            user_id = message.object.user_id

            if duel.get("accepted"):
                print(f"[join_duel] Дуэль {peer} уже принята другим игроком")
                await bot.api.messages.send_message_event_answer(
                    event_id=message.object.event_id,
                    peer_id=message.object.peer_id,
                    user_id=user_id,
                    event_data=json.dumps({"type": "show_snackbar", "text": "⚔️ В эту дуэль уже вступили!"})
                )
                return True

            if user_id == author:
                print("[join_duel] Игрок пытается вступить в свою же дуэль!")
                await bot.api.messages.send_message_event_answer(
                    event_id=message.object.event_id,
                    peer_id=message.object.peer_id,
                    user_id=user_id,
                    event_data=json.dumps({"type": "show_snackbar", "text": "Ты не можешь вступить в свою же дуэль!"})
                )
                return True

            
            balances = load_data(BALANCES_FILE)
            joiner = balances.get(str(user_id), get_balance(user_id))
            if joiner["wallet"] < stake:
                print(f"[join_duel] Недостаточно монет у {user_id}: {joiner['wallet']} < {stake}")
                await bot.api.messages.send_message_event_answer(
                    event_id=message.object.event_id,
                    peer_id=message.object.peer_id,
                    user_id=user_id,
                    event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно монет!"})
                )
                return True

            duel["accepted"] = True
            save_data(DUELS_FILE, duels)

            
            winner = random.choice([author, user_id])
            loser = user_id if winner == author else author
            print(f"[join_duel] Победитель: {winner}, Проигравший: {loser}")

            w_bal = balances.get(str(winner), get_balance(winner))
            l_bal = balances.get(str(loser), get_balance(loser))

            w_bal["wallet"] += stake
            w_bal["won"] += 1
            w_bal["won_total"] += stake

            l_bal["wallet"] -= stake
            l_bal["lost"] += 1
            l_bal["lost_total"] += stake

            balances[str(winner)] = w_bal
            balances[str(loser)] = l_bal
            save_data(BALANCES_FILE, balances)
            duels.pop(peer, None)
            save_data(DUELS_FILE, duels)
            print("[join_duel] Балансы обновлены и сохранены")

            
            try:
                w_info = await bot.api.users.get(user_ids=winner)
                l_info = await bot.api.users.get(user_ids=loser)
                w_name = f"{w_info[0].first_name} {w_info[0].last_name}"
                l_name = f"{l_info[0].first_name} {l_info[0].last_name}"
            except Exception as e:
                print(f"[join_duel] Ошибка получения имён: {e}")
                w_name = str(winner)
                l_name = str(loser)

            
            try:
                x_resp = await bot.api.messages.get_by_conversation_message_id(
                    peer_id=message.object.peer_id,
                    conversation_message_ids=duel["message_id"],
                    group_id=message.group_id
                )
                items = json.loads(x_resp.model_dump_json()).get('items', [])
                if items:
                    x_text = items[0]['text']
                    await bot.api.messages.edit(
                        peer_id=message.object.peer_id,
                        message=x_text,
                        conversation_message_id=duel["message_id"],
                        keyboard=None
                    )
                    print("[join_duel] Кнопки успешно убраны")
            except Exception as e:
                print(f"[join_duel] Ошибка при удалении кнопок: {e}")

            
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message=(
                    f"💥 Дуэль завершена!\n\n"
                    f"[id{winner}|{w_name}] vs [id{loser}|{l_name}]\n"
                    f"👑 Победитель: [id{winner}|{w_name}]\n\n"
                    f"💰 Он забирает {format_number(stake)}₽"
                ),
                random_id=0
            )
            if stake >= 10_000_000:
                await record_pass_progress(author, "duel_play", 1, bot)
                await record_pass_progress(user_id, "duel_play", 1, bot)
            print("[join_duel] Результат отправлен")
            print(f"[join_duel] Дуэль {peer} удалена из списка")
            return True

        except Exception as e:
            print(f"[join_duel] Общая ошибка: {e}")
            return True

    if command == "getban":
        target_user = payload.get("user")
        if not target_user:
            return True

        
        role = await get_role(user_id, chat_id)
        if not _role_can_satisfy_standard_min(role, 2):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Недостаточно прав для просмотра информации о блокировках!"})
            )
            return True

        
        try:
            await bot.api.messages.delete(
                group_id=message.group_id,
                peer_id=message.object.peer_id,
                cmids=message.object.conversation_message_id,
                delete_for_all=True
            )
        except:
            pass

        
        
        sql.execute("SELECT * FROM globalban WHERE user_id = ?", (target_user,))
        globalban_entry = sql.fetchone()
        sql.execute("SELECT * FROM gbanlist WHERE user_id = ?", (target_user,))
        gbanlist_entry = sql.fetchone()
        sql.execute("SELECT * FROM games_blacklist WHERE user_id = ?", (target_user,))
        games_blacklist_entry = sql.fetchone()

        globalbans_chats = ""
        if globalban_entry:
            globalbans_chats += f"Блокировка во всех беседах — @id{globalban_entry[1]} (Модератор) | {globalban_entry[2]} | {globalban_entry[3]} МСК (UTC+3)"
        else:
            globalbans_chats += "Блокировка во всех беседах — отсутствует"

        if gbanlist_entry:
            globalbans_chats += f"\nБлокировка в беседах игроков — @id{gbanlist_entry[1]} (Модератор) | {gbanlist_entry[2]} | {gbanlist_entry[3]} МСК (UTC+3)"
        else:
            globalbans_chats += "\nБлокировка в беседах игроков — отсутствует"

        if games_blacklist_entry:
            globalbans_chats += f"\nЧС игр (блокировка игровых команд) — @id{games_blacklist_entry[1]} (Модератор) | {games_blacklist_entry[2]} | {games_blacklist_entry[3]} МСК (UTC+3)"
        else:
            globalbans_chats += "\nЧС игр (блокировка игровых команд) — отсутствует"

        
        sql.execute("SELECT chat_id FROM chats")
        chats_list = sql.fetchall()
        bans = ""
        count_bans = 0
        i = 1
        for c in chats_list:
            chat_id_check = c[0]
            try:
                sql.execute(f"SELECT moder, reason, date FROM bans_{chat_id_check} WHERE user_id = ?", (target_user,))
                user_bans = sql.fetchall()
                if user_bans:
                    rel_id = 2000000000 + chat_id_check
                    try:
                        resp = await bot.api.messages.get_conversations_by_id(peer_ids=rel_id)
                        if resp.items:
                            chat_title = resp.items[0].chat_settings.title or "Без названия"
                        else:
                            chat_title = "Без названия"
                    except:
                        chat_title = "Ошибка получения названия"

                    count_bans += 1
                    for ub in user_bans:
                        mod, reason, date = ub
                        bans += f"{i}) {chat_title} | @id{mod} (Модератор) | {reason} | {date} МСК (UTC+3)\n"
                        i += 1
            except:
                continue

        if count_bans == 0:
            bans_chats = "Блокировки в беседах отсутствуют"
        else:
            bans_chats = f"Количество бесед, в которых заблокирован пользователь: {count_bans}\nИнформация о банах пользователя:\n{bans}"

        
        user_name = await get_user_name(target_user, chat_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            random_id=0,
            message=(
                f"Информация о блокировках @id{target_user} (Пользователь):\n\n"
                f"{globalbans_chats}\n\n"
                f"{bans_chats}"
            ),
            disable_mentions=1
        )
        return True

        if command == "kick_blacklisted":
            
            if not await _can_use_command(user_id, chat_id, 7):
                try:
                    await bot.api.messages.send_message_event_answer(
                        event_id=message.object.event_id,
                        peer_id=message.object.peer_id,
                        user_id=message.object.user_id,
                        event_data=json.dumps({
                            "type": "show_snackbar",
                            "text": "Недостаточно прав!"
                        })
                    )
                except:
                    pass
                return True

            
            sql.execute("SELECT user_id FROM blacklist")
            blacklisted = sql.fetchall()
            if not blacklisted:
                try:
                    await bot.api.messages.edit(
                        peer_id=message.peer_id,
                        conversation_message_id=message.conversation_message_id,
                        message="Не удалось исключить ни одного пользователя из ЧСБ.",
                        keyboard=None
                    )
                except:
                    pass
                return True

            kicked_users = ""
            i = 1
            for user_ban in blacklisted:
                user_ban_id = user_ban[0]
                try:
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, member_id=user_ban_id)
                    kicked_users += f"{i}. @id{user_ban_id} ({await get_user_name(user_ban_id, chat_id)})\n"
                    i += 1
                except:
                    pass  

            
            try:
                await bot.api.messages.edit(
                    peer_id=message.peer_id,
                    conversation_message_id=message.conversation_message_id,
                    message="Удаление пользователей в ЧСБ, завершено...",
                    keyboard=None
                )
            except:
                pass

            
            if kicked_users:
                await bot.api.messages.send(
                    peer_id=message.peer_id,
                    random_id=0,
                    message=(
                        f"@id{user_id} ({await get_user_name(user_id, chat_id)}), "
                        f"исключил(-а) пользователей в ЧСБ:\n\n{kicked_users}"
                    ),
                    disable_mentions=1
                )
            else:
                await bot.api.messages.send(
                    peer_id=message.peer_id,
                    random_id=0,
                    message="Не удалось исключить ни одного пользователя из ЧСБ.",
                    disable_mentions=1
                )

            return True            

    if command == "blacklistminus":
        if not await _can_use_command(user_id, chat_id, 8):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM blacklist ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page       

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mid, chat_id)
                except:
                    mod_name = str(mid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page - 1
        users = await get_page(new_page)
        text = "\n".join(users) if users else "Нет данных."

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "blacklistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "blacklistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в черном списке бота (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True


    if command == "blacklistplus":
        if not await _can_use_command(user_id, chat_id, 8):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM blacklist ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mid, chat_id)
                except:
                    mod_name = str(mid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page + 1
        users = await get_page(new_page)
        if new_page > max_page or len(users) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        text = "\n".join(users)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "blacklistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "blacklistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в черном списке бота (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True


    if command == "gbanlistminus":
        if not await _can_use_command(user_id, chat_id, 8):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM gbanlist UNION SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM globalban ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mid, chat_id)
                except:
                    mod_name = str(mid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page - 1
        users = await get_page(new_page)
        text = "\n".join(users) if users else "Нет данных."

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "gbanlistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "gbanlistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в глобальной блокировке (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True


    if command == "gbanlistplus":
        if not await _can_use_command(user_id, chat_id, 8):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM gbanlist UNION SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM globalban ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mid, chat_id)
                except:
                    mod_name = str(mid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page + 1
        users = await get_page(new_page)
        if new_page > max_page or len(users) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        text = "\n".join(users)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "gbanlistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "gbanlistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в глобальной блокировке (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "gamesbanlistminus":
        if not await _has_role_level(user_id, chat_id, 15):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM games_blacklist ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page - 1
        users = await get_page(new_page)
        text = "\n".join(users) if users else "Нет данных."

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "gamesbanlistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "gamesbanlistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в ЧС игр (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "gamesbanlistplus":
        if not await _has_role_level(user_id, chat_id, 15):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM games_blacklist ORDER BY datetime_globalban DESC")
        all_rows = sql.fetchall()
        total = len(all_rows)
        per_page = 20
        max_page = (total + per_page - 1) // per_page

        async def get_page(page):
            start = (page - 1) * per_page
            end = start + per_page
            data = all_rows[start:end]
            formatted = []
            for i, (uid, mid, reason, date) in enumerate(data, start=start + 1):
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                formatted.append(f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mid}|Модератор] | Причина: {reason} | Дата: {date}")
            return formatted

        new_page = page + 1
        users = await get_page(new_page)
        if new_page > max_page or len(users) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        text = "\n".join(users)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "gamesbanlistminus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "gamesbanlistplus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Список пользователей в ЧС игр (страница {new_page}/{max_page}):\n\n{text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "infoidminus":
        page = payload.get("page")
        target = payload.get("user")

        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT chat_id FROM chats WHERE owner_id = ?", (target,))
        user_chats = sql.fetchall()
        per_page = 5
        start = (page - 2) * per_page
        end = start + per_page
        page_chats = user_chats[start:end]

        all_chats = []
        for idx, (chat_id_val,) in enumerate(page_chats, start=1):
            chat_title, link = await get_chat_display_info(chat_id_val)
            all_chats.append(
                f"{idx}. {chat_title} | 🆔: {chat_id_val} | 🔗 {format_chat_link_text(link)}"
            )

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Назад", {"command": "infoidMinus", "page": page - 1, "user": target}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Вперёд", {"command": "infoidPlus", "page": page - 1, "user": target}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        all_chats_text = "\n".join(all_chats)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"❗ Список бесед @id{target} (пользователя):\n(Страница: {page - 1})\n\n{all_chats_text}\n\n🗨️ Всего бесед у пользователя: {idx}",
            random_id=0,
            disable_mentions=1,
            keyboard=keyboard
        )

    if command in {"mlogs_minus", "mlogs_plus", "smlogs_minus", "smlogs_plus"}:
        if not await _can_use_command(user_id, chat_id, 3):
            await answer_callback_event(message, "Недостаточно прав!")
            return True

        current_page = int(payload.get("page", 1) or 1)
        direction = -1 if command.endswith("_minus") else 1
        requested_page = current_page + direction
        is_network = command.startswith("smlogs")
        target = payload.get("target")

        if requested_page < 1:
            await answer_callback_event(message, "Это первая страница!")
            return True

        if is_network:
            scope_chat_ids = await get_gsync_chats(chat_id)
            if not scope_chat_ids:
                await answer_callback_event(message, "Беседа больше не подключена к сетке")
                return True
        else:
            scope_chat_ids = [chat_id]

        try:
            logs = await _read_moderation_logs(scope_chat_ids, int(target) if target is not None else None)
        except Exception as error:
            print(f"[MODERATION LOGS CALLBACK ERROR] command={command} user={user_id} chat={chat_id}: {error}")
            await answer_callback_event(message, "Не удалось прочитать логи. Попробуйте ещё раз.")
            return True
        formatted, page, max_page = await _format_moderation_log_page(
            logs, requested_page, chat_id, show_chat=is_network
        )
        if requested_page > max_page or not formatted:
            await answer_callback_event(message, "Это последняя страница!")
            return True

        base_callback = "smlogs" if is_network else "mlogs"
        payload_common = {"page": page, "chatId": chat_id}
        if target is not None:
            payload_common["target"] = int(target)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": f"{base_callback}_minus", **payload_common}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": f"{base_callback}_plus", **payload_common}), color=KeyboardButtonColor.POSITIVE)
        )
        title = "Модерационные логи сетки бесед" if is_network else "Модерационные логи беседы"
        if target is not None:
            title += f" пользователя @id{target}"
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"{title} [{page}/{max_page}]:\n\n" + "\n\n".join(formatted),
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "modersallminus":
        if not await _has_role_level(user_id, chat_id, 10):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        sql.execute("SELECT * FROM logchats ORDER BY rowid DESC LIMIT 9999999999999")
        logs = sql.fetchall()

        total = len(logs)
        per_page = MAX_LOGS
        max_page = (total + per_page - 1) // per_page

        async def get_moders_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            selected = logs[start:end]
            formatted = []
            for idx, entry in enumerate(selected, start=start + 1):
                u_id, t_id, amount, log_text, *rest = entry

                try:
                    u_info = await bot.api.users.get(user_ids=u_id)
                    u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                except:
                    u_name = str(u_id)

                if t_id:
                    try:
                        t_info = await bot.api.users.get(user_ids=t_id)
                        t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                        t_display = f"@id{t_id} ({t_name})"
                    except:
                        t_display = f"@id{t_id}"
                else:
                    t_display = "None"

                created_at = rest[0] if rest else "Дата не записана"
                entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                try:
                    a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                except Exception:
                    a_display = "Пользователь"
                l_display = log_text if log_text else "—"

                formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
            return formatted

        new_page = page - 1
        moders_page = await get_moders_page(new_page)
        moders_text = "\n\n".join(moders_page)

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "modersAllMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "modersAllPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Общие логи модерации [{new_page}/{max_page}]:\n\n{moders_text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "modersallplus":
        if not await _has_role_level(user_id, chat_id, 10):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        sql.execute("SELECT * FROM logchats ORDER BY rowid DESC LIMIT 9999999999999")
        logs = sql.fetchall()

        total = len(logs)
        per_page = MAX_LOGS
        max_page = (total + per_page - 1) // per_page

        async def get_moders_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            selected = logs[start:end]
            formatted = []
            for idx, entry in enumerate(selected, start=start + 1):
                u_id, t_id, amount, log_text, *rest = entry

                try:
                    u_info = await bot.api.users.get(user_ids=u_id)
                    u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                except:
                    u_name = str(u_id)

                if t_id:
                    try:
                        t_info = await bot.api.users.get(user_ids=t_id)
                        t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                        t_display = f"@id{t_id} ({t_name})"
                    except:
                        t_display = f"@id{t_id}"
                else:
                    t_display = "None"

                created_at = rest[0] if rest else "Дата не записана"
                entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                try:
                    a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                except Exception:
                    a_display = "Пользователь"
                l_display = log_text if log_text else "—"

                formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
            return formatted

        new_page = page + 1
        moders_page = await get_moders_page(new_page)
        if len(moders_page) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        moders_text = "\n\n".join(moders_page)

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "modersAllMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("⏩", {"command": "modersAllPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"Общие логи модерации [{new_page}/{max_page}]:\n\n{moders_text}",
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True
        
    if command == "modersminus":
        if not await _has_role_level(user_id, chat_id, 10):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        target = payload.get("target")

        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        if target:
            sql.execute("SELECT * FROM logchats WHERE user_id = ? ORDER BY rowid DESC LIMIT 9999999999999", (target,))
            logs = sql.fetchall()
        else:
            sql.execute("SELECT * FROM logchats ORDER BY rowid DESC LIMIT 9999999999999")
            logs = sql.fetchall()

        total = len(logs)
        per_page = MAX_LOGS
        max_page = (total + per_page - 1) // per_page

        async def get_moders_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            selected = logs[start:end]
            formatted = []
            for idx, entry in enumerate(selected, start=start + 1):
                u_id, t_id, amount, log_text, *rest = entry

                try:
                    u_info = await bot.api.users.get(user_ids=u_id)
                    u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                except:
                    u_name = str(u_id)

                if t_id:
                    try:
                        t_info = await bot.api.users.get(user_ids=t_id)
                        t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                        t_display = f"@id{t_id} ({t_name})"
                    except:
                        t_display = f"@id{t_id}"
                else:
                    t_display = "None"

                created_at = rest[0] if rest else "Дата не записана"
                entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                try:
                    a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                except Exception:
                    a_display = "Пользователь"
                l_display = log_text if log_text else "—"

                formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
            return formatted

        new_page = page - 1
        moders_page = await get_moders_page(new_page)
        moders_text = "\n\n".join(moders_page)

        if target:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "modersMinus", "target": target, "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("⏩", {"command": "modersPlus", "target": target, "page": new_page}), color=KeyboardButtonColor.POSITIVE)
            )
            message_text = f"Логи модерации @id{target} ({await get_user_name(target, chat_id)}) [{new_page}/{max_page}]:\n\n{moders_text}"
        else:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "modersAllMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("⏩", {"command": "modersAllPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
            )
            message_text = f"Общие логи модерации [{new_page}/{max_page}]:\n\n{moders_text}"

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=message_text,
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True

    if command == "modersplus":
        if not await _has_role_level(user_id, chat_id, 10):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        target = payload.get("target")

        if target:
            sql.execute("SELECT * FROM logchats WHERE user_id = ? ORDER BY rowid DESC LIMIT 9999999999999", (target,))
            logs = sql.fetchall()
        else:
            sql.execute("SELECT * FROM logchats ORDER BY rowid DESC LIMIT 9999999999999")
            logs = sql.fetchall()

        total = len(logs)
        per_page = MAX_LOGS
        max_page = (total + per_page - 1) // per_page

        async def get_moders_page(page: int):
            start = (page - 1) * per_page
            end = start + per_page
            selected = logs[start:end]
            formatted = []
            for idx, entry in enumerate(selected, start=start + 1):
                u_id, t_id, amount, log_text, *rest = entry

                try:
                    u_info = await bot.api.users.get(user_ids=u_id)
                    u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                except:
                    u_name = str(u_id)

                if t_id:
                    try:
                        t_info = await bot.api.users.get(user_ids=t_id)
                        t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                        t_display = f"@id{t_id} ({t_name})"
                    except:
                        t_display = f"@id{t_id}"
                else:
                    t_display = "None"

                created_at = rest[0] if rest else "Дата не записана"
                entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                try:
                    a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                except Exception:
                    a_display = "Пользователь"
                l_display = log_text if log_text else "—"

                formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
            return formatted

        new_page = page + 1
        moders_page = await get_moders_page(new_page)
        if len(moders_page) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        moders_text = "\n\n".join(moders_page)

        if target:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "modersMinus", "target": target, "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("⏩", {"command": "modersPlus", "target": target, "page": new_page}), color=KeyboardButtonColor.POSITIVE)
            )
            message_text = f"Логи модерации @id{target} ({await get_user_name(target, chat_id)}) [{new_page}/{max_page}]:\n\n{moders_text}"
        else:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "modersAllMinus", "page": new_page}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("⏩", {"command": "modersAllPlus", "page": new_page}), color=KeyboardButtonColor.POSITIVE)
            )
            message_text = f"Общие логи модерации [{new_page}/{max_page}]:\n\n{moders_text}"

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=message_text,
            disable_mentions=1, random_id=0, keyboard=keyboard
        )
        return True        

        
    if command == "infoidplus":
        page = payload.get("page")
        target = payload.get("user")

        if not await _has_role_level(user_id, chat_id, 11):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        sql.execute("SELECT chat_id FROM chats WHERE owner_id = ?", (target,))
        user_chats = sql.fetchall()
        per_page = 5
        total_pages = (len(user_chats) + per_page - 1) // per_page

        if page >= total_pages:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        start = page * per_page
        end = start + per_page
        page_chats = user_chats[start:end]

        all_chats = []
        for idx, (chat_id_val,) in enumerate(page_chats, start=1):
            chat_title, link = await get_chat_display_info(chat_id_val)
            all_chats.append(
                f"{idx}. {chat_title} | 🆔: {chat_id_val} | 🔗 {format_chat_link_text(link)}"
            )

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Назад", {"command": "infoidMinus", "page": page + 1, "user": target}), color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Вперёд", {"command": "infoidPlus", "page": page + 1, "user": target}), color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        all_chats_text = "\n".join(all_chats)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"❗ Список бесед @id{target} (пользователя):\n(Страница: {page + 1})\n\n{all_chats_text}\n\nВсего бесед: {idx}",
            random_id=0,
            disable_mentions=1,
            keyboard=keyboard
        )        
              
    if command == "alt":
        user_role = await get_role(user_id, chat_id)
        if not _role_can_satisfy_standard_min(user_role, 1):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        commands_levels = {
            1: [
                '\nКоманды модераторов:',
                '/setnick — snick, nick, addnick, ник, сетник, аддник',
                '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                '/getnick — gnick, гник, гетник',
                '/getacc — acc, гетакк, аккаунт, account',
                '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                '/kick — кик, исключить',
                '/warn — пред, варн, pred, предупреждение',
                '/unwarn — унварн, анварн, снятьпред, минуспред',
                '/getwarn — gwarn, getwarns, гетварн, гварн',
                '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                '/warnlist — warns, wlist, варны, варнлист',
                '/staff — стафф',
                '/reg — registration, regdate, рег, регистрация, датарегистрации',
                '/mute — мут, мьют, муте, addmute',
                '/unmute — снятьмут, анмут, унмут, снятьмут',
                '/alt — альт, альтернативные',
                '/getmute -- gmute, гмут, гетмут, чекмут',
                '/mutelist -- mutes, муты, мутлист',
                '/clear -- чистка, очистить, очистка',
                '/getban -- чекбан, гетбан, checkban',
                '/delete -- удалить',
                '/chatid -- чатайди, айдичата'
            ],
            2: [
                '\nКоманды старших модераторов:',
                '/ban — бан, блокировка',
                '/unban -- унбан, снятьбан',
                '/addmoder -- moder, модер',
                '/removerole -- rrole, снятьроль',
                '/zov - зов, вызов',
                '/online - ozov, озов',
                '/onlinelist - olist, олист',
                '/banlist - bans, банлист, баны',
                '/inactive - ilist, inactive'
            ],
            3: [
                '\nКоманды администраторов:',
                '/quiet -- silence, тишина',
                '/skick -- скик, снят',
                '/sban -- сбан',
                '/sunban — сунбан, санбан',
                '/addsenmoder — senmoder, смодер',
                '/rnickall -- allrnick, arnick, mrnick',
                '/sremovenick -- srnick',
                '/szov -- serverzov, сзов',
                '/srole -- none',
                '/ssetnick -- ssnick, ссник'
            ],
            4: [
                '\nКоманды старших администраторов:',
                '/addadmin -- admin, админ',
                '/serverinfo -- серверинфо',
                '/filter -- none',
                '/sremoverole -- srrole',
                '/bug -- баг',
                '/report -- реп, rep, жалоба'
            ],
            5: [
                '\nКоманды зам. спец. администраторов:',
                '/addsenadmin -- senadm, addsenadm, senadmin, садмин',
                '/sync -- синхронизация, сунс, синхронка',
                '/pin -- закрепить, пин',
                '/unpin -- открепить, унпин',
                '/deleteall -- удалитьвсе',
                '/gsinfo -- none',
                '/gsrnick -- none',
                '/gssnick -- none',
                '/gskick -- none',
                '/gsban -- none',
                '/gsunban -- none'
            ],
            6: [
                '\nКоманды спец. администраторов:',
                '/addzsa -- zsa, зса',
                '/server -- сервер',
                '/settings -- настройки',
                '/clearwarn -- очиститьварны',
                '/title -- none',
                '/antisliv -- антислив'
            ],
            7: [
                '\nСписок команд владельца беседы',
                '/addsa -- sa, са, spec, specadm',
                '/antiflood -- af',
                '/welcometext -- welcome, wtext',
                '/invite -- none',
                '/leave -- none',
                '/editowner -- owner',
                '/защита -- protection',
                '/settingsmute -- настройкимута',
                '/setinfo -- установитьинфо',
                '/setrules -- установитьправила',
                '/type -- тип',
                '/gsync -- привязка',
                '/gunsync -- удалитьпривязку',
                '/masskick - mkick',
                '/amnesty -- амнистия',
                '/settingsgame -- настройкиигр',
                '/settingsphoto -- настройкифото'
            ]
        }

        commands = []
        for i in commands_levels.keys():
            if _role_can_satisfy_standard_min(user_role, i):
                for b in commands_levels[i]:
                    commands.append(b)

        level_commands = '\n'.join(commands)

        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=f"Альтернативные команды\n\n{level_commands}",
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
                                                                       
async def handle_ep_shop_text_input(message: Message, user_id: int, chat_id: int) -> bool:
    state = ep_shop_inputs.get(int(user_id))
    if not state:
        return False

    raw_text = (message.text or "").strip()
    if not raw_text or raw_text.startswith(BOT_COMMAND_PREFIXES):
        return False

    if time.time() - float(state.get("created_at", 0) or 0) > EP_INPUT_TTL_SECONDS:
        ep_shop_inputs.pop(int(user_id), None)
        await message.reply("Время ввода количества EP истекло. Откройте /епоинтс заново.", disable_mentions=1)
        return True

    role = await get_role(user_id, chat_id)
    if not _role_can_satisfy_standard_min(role, 9):
        ep_shop_inputs.pop(int(user_id), None)
        await message.reply("Недостаточно прав!", disable_mentions=1)
        return True

    cat = str(state.get("cat", ""))
    if cat == "phone":
        phone = raw_text.strip()
        amount = int(state.get("amount", 0) or 0)
        rub = float(state.get("rub", 0) or 0)
        # deduct EP now
        spent = await spend_epoints(user_id, amount, reason="ep_withdraw")
        if not spent:
            ep_shop_inputs.pop(int(user_id), None)
            await message.reply("Не удалось списать Epoints. Попробуйте ещё раз.", disable_mentions=1)
            return True
        # notify developers
        dev_ids = [488828183, 574393629]
        text = f"💸 Заявка на вывод\nПользователь: @id{user_id}\nСумма: {amount} EP ({rub:.2f}₽)\nРеквизиты: {phone}"
        for dev in dev_ids:
            try:
                await bot.api.messages.send(peer_id=dev, message=text, random_id=0, disable_mentions=1)
            except Exception:
                pass
        ep_shop_inputs.pop(int(user_id), None)
        remaining_ep = await get_total_epoints(user_id)
        await message.reply(
            f"✅ Заявка отправлена. {format_number(amount)} EP списано.\n💎 Баланс: {format_number(remaining_ep)} Epoints",
            disable_mentions=1,
        )
        return True

    quantity = _parse_ep_quantity_text(raw_text)
    if quantity is None:
        await message.reply("Введите количество EP числом. Например: 10", disable_mentions=1)
        return True

    total_ep = await get_total_epoints(user_id)
    if total_ep < quantity:
        await message.reply(f"Недостаточно Epoints. Нужно {format_number(quantity)} EP, у вас {format_number(total_ep)} EP.", disable_mentions=1)
        return True

    cat = str(state.get("cat", ""))
    if cat not in {"money", "bananas"}:
        ep_shop_inputs.pop(int(user_id), None)
        return False

    spent = await spend_epoints(user_id, quantity, reason=f"ep_shop:{cat}")
    if not spent:
        await message.reply("Не удалось списать Epoints. Попробуйте ещё раз.", disable_mentions=1)
        return True

    bal = get_balance(user_id)
    if cat == "money":
        amount_rub = quantity * 0.01
        await log_economy(user_id=user_id, target_id=None, amount=amount_rub, log=f"заявка на вывод {quantity} EP ({amount_rub:.2f}₽)")
        ep_shop_inputs[user_id] = {"cat": "phone", "amount": quantity, "rub": amount_rub, "created_at": time.time()}
        await message.reply(
            f"💸 Заявка на вывод: {format_number(quantity)} EP → {amount_rub:.2f}₽\n\nВведите номер телефона или карты для оплаты:",
            disable_mentions=1,
        )
        return True

    ep_shop_inputs.pop(int(user_id), None)
    remaining_ep = await get_total_epoints(user_id)
    kb = Keyboard(inline=True)
    kb.add(Callback("Назад в /епоинтс", {"command": "ep_back", "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)
    await message.reply(f"💎 Баланс: {format_number(remaining_ep)} Epoints", keyboard=kb, disable_mentions=1)
    return True



@bot.on.chat_message()
async def on_chat_message(message: Message):
    global balances
    bot_identifiers = BOT_COMMAND_PREFIXES
    user_id = message.from_id
    chat_id = message.chat_id
    _CURRENT_LOG_CHAT_ID.set(chat_id)
    peer_id = message.peer_id
    conversation_message_id = getattr(message, "conversation_message_id", None)

    
    try:
        pass
    except:
        pass

    if conversation_message_id is not None and should_skip_duplicate_chat_event(peer_id, conversation_message_id):
        return True

    await ensure_runtime_schema()

    raw_text = message.text or ""
    arguments = raw_text.split()
    args = raw_text.split()
    arguments_lower = raw_text.lower().split()
    args_lower = raw_text.lower().split()
    userf = f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
    pending_exchange_draft = None
    if pending_exchange_draft and pending_exchange_draft.get("awaiting_custom_bonus"):
        raw_amount_text = (message.text or "").strip()
        if raw_amount_text.isdigit():
            bonus_amount = int(raw_amount_text)
            bonus_side = str(pending_exchange_draft.get("awaiting_bonus_side") or "from")
            test_draft = dict(pending_exchange_draft)
            if bonus_side == "to":
                test_draft["to_money_bonus"] = bonus_amount
            else:
                test_draft["from_money_bonus"] = bonus_amount
            funds_error = _get_exchange_funds_error(
                int(test_draft["from_user_id"]),
                int(test_draft["to_user_id"]),
                int(test_draft.get("from_money_bonus", 0) or 0),
                int(test_draft.get("to_money_bonus", 0) or 0),
            )
            if funds_error:
                await message.reply(funds_error, disable_mentions=1)
                return True
            pending_exchange_draft = test_draft
            pending_exchange_draft["awaiting_bonus_side"] = None
            pending_exchange_draft["awaiting_custom_bonus"] = False
            property_exchange_drafts[int(user_id)] = pending_exchange_draft
            if bonus_side == "from":
                await message.reply(
                    f"✅ Первая сторона настроена.\nТеперь выберите для [id{pending_exchange_draft['to_user_id']}|{pending_exchange_draft['to_name']}]: имущество или доплата.",
                    keyboard=await _build_exchange_side_start_keyboard(user_id, "to"),
                    disable_mentions=1,
                )
            else:
                await _send_exchange_offer_from_draft(message.peer_id, pending_exchange_draft)
            return True
        if raw_amount_text and not raw_amount_text.startswith(tuple(bot_identifiers)):
            await message.reply("Введите сумму доплаты числом, например: 250000", disable_mentions=1)
            return True

    
    pass
    banned_chat_row = await db_execute("SELECT chat_id FROM banschats WHERE chat_id = ?", (chat_id,), fetch='one')
    is_registered = await check_chat(chat_id)

    
    if banned_chat_row and not is_registered:
        sql.execute("DELETE FROM banschats WHERE chat_id = ?", (chat_id,))
        database.commit()
        await new_chat(chat_id, peer_id, user_id, "def")
        database.commit()
        print(f"[CHAT RESTORED] Чат {chat_id} восстановлен из banschats при сообщении")
        is_registered = True
    
    if banned_chat_row and is_registered:
        await message.reply("Владелец беседы, не член ADMIN MANAGER! Я не буду здесь работать.")
        return True

    if is_registered:
        try:
            await _record_admin_message_activity(user_id, chat_id)
        except Exception as e:
            print(f"[STATS] Ошибка учета сообщения администратора: {e}")

    if await handle_ep_shop_text_input(message, user_id, chat_id):
        return True

    
    if user_id in property_exchange_drafts and property_exchange_drafts[user_id].get("mode") == "reset_request":
        draft = property_exchange_drafts[user_id]

        if draft.get("awaiting_proof"):
            print(f"[AF_TECH DEBUG] awaiting_proof=True, attachments={len(message.attachments) if message.attachments else 0}, text={repr(message.text)}")
            proof_photo = ",".join(extract_message_media_attachments(message))
            if not proof_photo:
                print(f"[AF_TECH] No proof photo found. attachments={message.attachments}, text={message.text}")
                await bot.api.messages.send(peer_id=peer_id, message="❌ Нужно прикрепить фото или видео! Отправьте сообщение с вложенным медиа.", random_id=0, disable_mentions=1)
                return True

            proof_cmid = message.conversation_message_id
            draft["awaiting_proof"] = False
            draft["proof_photo"] = proof_photo
            draft["awaiting_reason"] = True
            property_exchange_drafts[user_id] = draft

            type_name = "ВСЁ имущество" if draft["property_type"] == "all" else get_property_type_name(draft["property_type"])

            kb = Keyboard(inline=True)
            kb.add(Callback("⚠️ Без причины", {"command": "aфtech_confirm", "target": draft["target_user_id"], "target_name": draft["target_user_name"], "ptype": draft["property_type"], "owner_id": user_id}))
            kb.row()
            kb.add(Callback("⬅️ Назад (изменить фото)", {"command": "aфtech_back_proof", "target": draft["target_user_id"], "target_name": draft["target_user_name"], "ptype": draft["property_type"], "owner_id": user_id}))
            kb.add(Callback("❌ Отменить", {"command": "aфtech_cancel", "owner_id": user_id}))

            draft["cmid"] = draft.get("cmid") or message.object.conversation_message_id
            try:
                await bot.api.messages.edit(
                    peer_id=peer_id,
                    message=(
                        f"📋 Заявка на обнуление\n\n"
                        f"👤 Цель: [id{draft['target_user_id']}|{draft['target_user_name']}]\n"
                        f"📦 Раздел: {type_name}\n"
                        f"📎 Доказательство получено\n\n"
                        f"📝 Введите причину обнуления (текстом):"
                    ),
                    keyboard=kb,
                    conversation_message_id=draft["cmid"],
                    disable_mentions=1
                )
            except:
                await bot.api.messages.send(
                    peer_id=peer_id,
                    message=(
                        f"📋 Заявка на обнуление\n\n"
                        f"👤 Цель: [id{draft['target_user_id']}|{draft['target_user_name']}]\n"
                        f"📦 Раздел: {type_name}\n"
                        f"📎 Доказательство получено\n\n"
                        f"📝 Введите причину обнуления (текстом):"
                    ),
                    keyboard=kb,
                    random_id=0,
                    disable_mentions=1
                )
            await delete_message(message.group_id, peer_id, proof_cmid)
            return True

        if draft.get("awaiting_reason"):
            raw_reason = (message.text or "").strip()
            reason_cmid = message.conversation_message_id
            draft["awaiting_reason"] = False
            draft["reason"] = raw_reason if raw_reason else "Не указана"
            property_exchange_drafts[user_id] = draft

            type_name = "ВСЁ имущество" if draft["property_type"] == "all" else get_property_type_name(draft["property_type"])
            reason_display = draft["reason"]

            kb = Keyboard(inline=True)
            kb.add(Callback("✅ Подтвердить", {"command": "aфtech_confirm", "target": draft["target_user_id"], "target_name": draft["target_user_name"], "ptype": draft["property_type"], "owner_id": user_id}))
            kb.row()
            kb.add(Callback("⬅️ Назад (изменить фото)", {"command": "aфtech_back_proof", "target": draft["target_user_id"], "target_name": draft["target_user_name"], "ptype": draft["property_type"], "owner_id": user_id}))
            kb.add(Callback("❌ Отменить", {"command": "aфtech_cancel", "owner_id": user_id}))

            try:
                await bot.api.messages.edit(
                    peer_id=peer_id,
                    message=(
                        f"📋 Заявка на обнуление\n\n"
                        f"👤 Цель: [id{draft['target_user_id']}|{draft['target_user_name']}]\n"
                        f"📦 Раздел: {type_name}\n"
                        f"📎 Доказательство получено\n"
                        f"📝 Причина: {reason_display}\n\n"
                        f"Нажмите «Подтвердить» для отправки:"
                    ),
                    keyboard=kb,
                    conversation_message_id=draft["cmid"],
                    disable_mentions=1
                )
            except:
                await bot.api.messages.send(
                    peer_id=peer_id,
                    message=(
                        f"📋 Заявка на обнуление\n\n"
                        f"👤 Цель: [id{draft['target_user_id']}|{draft['target_user_name']}]\n"
                        f"📦 Раздел: {type_name}\n"
                        f"📎 Доказательство получено\n"
                        f"📝 Причина: {reason_display}\n\n"
                        f"Нажмите «Подтвердить» для отправки:"
                    ),
                    keyboard=kb,
                    random_id=0,
                    disable_mentions=1
                )
            await delete_message(message.group_id, peer_id, reason_cmid)
            return True

        if draft.get("awaiting_amount"):
            raw_amount = (message.text or "").strip()

            if draft.get("property_type") == "all":
                amount = "all"
            elif not raw_amount.replace('.', '').replace(',', '').isdigit():
                await bot.api.messages.send(peer_id=peer_id, message="Введите числовое значение:", random_id=0, disable_mentions=1)
                return True
            else:
                amount = raw_amount
            amount_cmid = message.conversation_message_id
            draft["awaiting_amount"] = False
            draft["amount"] = amount
            draft["awaiting_proof"] = True
            property_exchange_drafts[user_id] = draft

            type_name = "ВСЁ имущество" if draft["property_type"] == "all" else get_property_type_name(draft["property_type"])

            kb = Keyboard(inline=True)
            kb.add(Callback("⬅️ Назад (изменить количество)", {"command": "aфtech_back_amount", "target": draft["target_user_id"], "target_name": draft["target_user_name"], "ptype": draft["property_type"], "owner_id": user_id}))
            kb.row()
            kb.add(Callback("❌ Отменить", {"command": "aфtech_cancel", "owner_id": user_id}))

            await bot.api.messages.send(
                peer_id=peer_id,
                message=(
                    f"💰 Количество: {amount}\n"
                    f"📦 Тип: {type_name}\n\n"
                    f"📎 Шаг 2: Теперь отправьте фото или видео как доказательство."
                ),
                keyboard=kb,
                random_id=0,
                disable_mentions=1
            )
            await delete_message(message.group_id, peer_id, amount_cmid)
            return True

        await bot.api.messages.send(peer_id=peer_id, message="⏳ Вы в процессе заполнения заявки. Отправьте требуемое сообщение (число или фото/видео).", random_id=0, disable_mentions=1)
        return True

    

    action = getattr(message, "action", None)
    action_type = getattr(action, "type", None)
    action_member_id = getattr(action, "member_id", None)
    if action_member_id is None and action_type == "chat_invite_user_by_link":
        action_member_id = user_id
    if is_registered and action and action_type in {"chat_invite_user_by_link", "chat_invite_user"} and action_member_id:
        welcome = await get_welcome(chat_id)
        should_send_welcome = action_type == "chat_invite_user_by_link" or int(action_member_id) == int(user_id)
        if welcome and should_send_welcome:
            welcome = welcome.replace('%u', f'@id{action_member_id}')
            welcome = welcome.replace('%n', f'@id{action_member_id} ({await get_user_name(action_member_id, chat_id)})')
            welcome = welcome.replace('%i', f'@id{user_id}')
            welcome = welcome.replace('%p', f'@id{user_id} ({await get_user_name(user_id, chat_id)})')
            await message.answer(welcome)
            return True

    if is_registered and await check_quit(chat_id):
        if not await _has_role_level(user_id, chat_id, 1):
            try:
                await bot.api.messages.delete(
                    group_id=message.group_id, 
                    peer_id=message.peer_id, 
                    delete_for_all=True, 
                    cmids=message.conversation_message_id
                )
                return True
            except Exception as error:
                print(f"[QUIET (/ТИШИНА)]:", error)
        else:
            pass       

    if is_registered and message.attachments and any("photo" in str(getattr(attach, "type", "") or "").lower() for attach in message.attachments):
        mode_data = await db_execute("SELECT mode FROM photosettings WHERE chat_id = ?", (chat_id,), fetch='one')
        mode = mode_data[0] if mode_data else 0

        if mode == 1:
            await message.reply(f"В данной беседе «№{chat_id}» запрещено отправлять фотографии!\n\nДанную настройку можно выключить в: «/settingsphoto»")
            await bot.api.messages.delete(
                group_id=message.group_id,
                peer_id=message.peer_id,
                delete_for_all=True,
                cmids=message.conversation_message_id
            )
            return True

    
    if is_registered and await get_filter(chat_id) and not await _can_use_command(user_id, chat_id, 1):
        try:
            banned_words = await get_active_banwords(chat_id)
            text_lower = (message.text or "").lower()
            for word in banned_words:
                if word in text_lower:
                    admin = "bananamanagerbot"
                    reason = "Написание запрещенных слов"
                    mute_time = 30

                    await add_mute(user_id, chat_id, admin, reason, mute_time)

                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Снять мут", {"command": "unmute", "user": user_id, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                    )

                    await message.replyLocalizedMessage('mute_is_banwords', {
                        'user': userf
                    })

                    await bot.api.messages.delete(
                        group_id=message.group_id,
                        peer_id=message.peer_id,
                        delete_for_all=True,
                        cmids=message.conversation_message_id
                    )
                    return True
        except Exception as e:
            print(f"[BANWORDS] Ошибка проверки слов: {e}")            

    
    if is_registered and await get_mute(user_id, chat_id) and not await checkMute(chat_id, user_id):
        mode_data = await db_execute("SELECT mode FROM mutesettings WHERE chat_id = ?", (chat_id,), fetch='one')
        mode = mode_data[0] if mode_data else 0

        warns = await get_warns(user_id, chat_id)

        if mode == 1:
            if warns < 3:
                bot_name = "blackrussiamanagerbot"
                reason = "Написание слов в муте"
                await warn(chat_id, user_id, bot_name, reason)
                await message.replyLocalizedMessage('mute_is_warn', {
                    'user': userf,
                    'warns': warns                    
                })
                await bot.api.messages.delete(
                    group_id=message.group_id,
                    peer_id=message.peer_id,
                    delete_for_all=True,
                    cmids=message.conversation_message_id
                )
            else:
                try:
                    await bot.api.messages.remove_chat_user(chat_id, user_id)
                    await message.replyLocalizedMessage('limit_warns_kick', {
                        'user': userf
                    })
                    await clear_warns(chat_id, user_id)
                except:
                    await message.replyLocalizedMessage('not_this_kick', {
                        'user': f'@id{user_id} (пользователя)'
                    })
                    await clear_warns(chat_id, user_id)
        else:
            await bot.api.messages.delete(
                group_id=message.group_id,
                peer_id=message.peer_id,
                delete_for_all=True,
                cmids=message.conversation_message_id
            )

    
    if is_registered:
        blacklist_entry = await db_execute("SELECT user_id FROM blacklist WHERE user_id = ?", (user_id,), fetch='one')
        if blacklist_entry:
            await message.reply("Вы в ЧСБ ADMIN MANAGER, использование команд бота запрещено.", disable_mentions=1)
            return True
            return True

    
    try:
        first_argument = arguments[0].strip()
        minus_rep_invocation = bool(re.fullmatch(r"-(?:rep|реп)(?:@[a-zA-Z0-9_.]+)?", first_argument, flags=re.IGNORECASE))
        if is_bot_command_invocation(first_argument) or minus_rep_invocation:
            command_identifier = first_argument[0]
            command = first_argument[1:].lower()
            
            
            if "@" in command:
                command = command.split("@", 1)[0]
        else:
            command_identifier = " "
            command = " "
    except:
        command_identifier = " "
        command = " "

    if command_identifier in bot_identifiers or command_identifier == "-":
        
        
        bot_target_allowed_commands = {
            "delete", "удалить",
            "clear", "чистка", "очистить", "очистка",
            "pin", "unpin",
        }
        if command not in bot_target_allowed_commands and _message_targets_own_bot(message):
            await message.reply("Недостаточно прав!", disable_mentions=1)
            return True


        
        if command_identifier in {"+", "-"} and command in {"rep", "реп"}:
            target = await extract_user_id(message)
            if not target:
                await message.reply(
                    "Укажите пользователя ответом на сообщение, пересланным сообщением, упоминанием или ссылкой.",
                    disable_mentions=1,
                )
                return True
            target = int(target)
            if target == int(user_id):
                await message.reply("Нельзя изменять репутацию самому себе.", disable_mentions=1)
                return True

            direction = 1 if command_identifier == "+" else -1
            changed, new_value = await change_reputation(chat_id, user_id, target, direction)
            if not changed:
                await message.reply("Вы уже поставили данному человеку репутацию", disable_mentions=1)
                return True

            sender_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(target, chat_id)
            action = "выдал реп" if direction > 0 else "отнял реп"
            await message.reply(
                f"[id{user_id}|{sender_name}] {action} [id{target}|{target_name}]\n"
                f"Теперь у него: {new_value} Репутации",
                disable_mentions=1,
            )
            return True

        if command_identifier == "/" and command in {"rep", "реп"}:
            if len(arguments) > 1 or message.reply_message or message.fwd_messages:
                await message.reply(
                    "Для изменения репутации используйте +rep (+реп) или -rep (-реп).",
                    disable_mentions=1,
                )
                return True
            value = await get_reputation(chat_id, user_id)
            name = await get_user_name(user_id, chat_id)
            await message.reply(
                f"[id{user_id}|{name}]\nРепутация: {value}",
                disable_mentions=1,
            )
            return True

        if command_identifier == "/" and command in {"топреп", "toprep", "reptop"}:
            top_rows = await get_reputation_top(chat_id, 20)
            if not top_rows:
                await message.reply("В глобальном топе пока нет пользователей с положительной репутацией.", disable_mentions=1)
                return True
            lines = []
            for index, (top_user_id, reputation_value) in enumerate(top_rows, start=1):
                try:
                    top_name = await get_user_name(int(top_user_id), chat_id)
                except Exception:
                    top_name = str(top_user_id)
                lines.append(
                    f"{index}. [id{int(top_user_id)}|{top_name}] | {int(reputation_value)} репутации"
                )
            await message.reply(
                "Глобальный топ репутации:\n\n" + "\n\n".join(lines),
                disable_mentions=1,
            )
            return True
        

        pass
        if is_registered and get_block_game(chat_id) and command in GAME_COMMANDS and command not in ['settingsgame', 'настройкиигр']:
            await message.reply("В данной беседе запрещено использовать любые игровые команды!\n\nВыключить данную настройку можно в: «/settingsgame»")
            return True
        if command in {"крупье", "dealers"}:
            dealers_text, dealers_keyboard, _, _ = await _dice_dealers_page(1)
            await message.reply(
                dealers_text,
                keyboard=dealers_keyboard,
                disable_mentions=1,
            )
            return True


        if command in ['start', 'старт', 'активировать']:
            if await check_chat(chat_id):
                await message.reply("Бот был ранее активирован в данной беседе!", disable_mentions=1)
                return True
            await new_chat(chat_id, peer_id, user_id)
            await message.reply("Беседа успешно занесена в базу данных бота!\n\nИспользуйте «/help» для ознакомления списка команд!", disable_mentions=1)
            return True  

        
        if command in ["form", "форма"]:
            if chat_id != 1:
                await message.reply(
                    "❗ Команда доступна только [https://vk.me/join/OuYg9/aZJxJdh/8hTaNzoqk543xct/EUk1g=|в формах на блокировку]"
                )
                return True

            
            target = None
            reason = "Не указано"
            if message.reply_message:
                target = message.reply_message.from_id
                if len(arguments) > 1:
                    reason = get_raw_text_tail(raw_text, 1)
            elif len(arguments) > 1 and await getID(arguments[1]):
                target = await getID(arguments[1])
                if len(arguments) > 2:
                    reason = get_raw_text_tail(raw_text, 2)
            else:
                await message.reply("Укажите пользователя через реплай или ID!")
                return True

            if target == bansids:
                await message.reply(f"Вы не можете подать форму на данного @id{target} (пользователя)")
                return True

            sender_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(target, chat_id)
            name = datetime.now().strftime("%I:%M:%S %p")

            
            keyboard = (
                Keyboard(inline=True)
                .add(
                    Callback(
                        "Одобрить",
                        {"command": "approve_form", "target": target, "sender": user_id, "reason": reason},
                    ),
                    color=KeyboardButtonColor.POSITIVE,
                )
                .add(
                    Callback(
                        "Отказать",
                        {"command": "reject_form", "target": target, "sender": user_id, "reason": reason},
                    ),
                    color=KeyboardButtonColor.NEGATIVE,
                )
            )

            
            await message.reply(
                (
                    f"📌 | Форма на «/gbanpl»:\n"
                    f"1. Пользователь: @id{user_id} ({sender_name})\n"
                    f"2. Нарушитель: @id{target} ({target_name})\n"
                    f"3. Причина: {reason}\n"
                    f"4. Дата подачи формы: {name} МСК (UTC+3)"
                ),
                keyboard=keyboard,
            )
            return True            

        if command in ['id', 'ид', 'getid', 'гетид', 'получитьид', 'giveid']:
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                user = user_id
            if user < 0:
                await message.replyLocalizedMessage('command_getid_group', {
                        'target': f'[club{abs(user)}|сообщества]',
                        'link': f'https://vk.ru/club{abs(user)}'
                    })
                return True
            await message.replyLocalizedMessage('command_getid_user', {
                        'target': f'@id{user} (пользователя)',
                        'link': f'https://vk.ru/id{user}'
                    })

        if command_identifier not in bot_identifiers and message.reply_message and message.reply_message.from_id < 0:
            return True     

        
        if command in ["offer", "предложение"]:
            try:
                user_info = await bot.api.users.get(user_ids=user_id)
                full_name = f"{user_info[0].first_name} {user_info[0].last_name}"
            except:
                full_name = f"id{user_id} (Ошибка)"

            args = message.text.split(maxsplit=1)
            offer = args[1].strip() if len(args) > 1 else ""
            if len(arguments) < 2 or len(offer) < 5:
                await message.reply("Укажите предложение по улучшению!")
                return True
            
            ok, error_text = await send_staff_feedback_message(
                f"⭐ | Предложение по улучшению бота:\n"
                f"1. Пользователь: [id{user_id}|{full_name}]\n"
                f"2. Предложение по улучшению: {offer}\n"
                f"3. Беседа: ID {chat_id}",
                source_user_id=user_id,
                feedback_type="offer",
            )
            if not ok:
                await message.reply(
                    f"⚠️ Не удалось отправить предложение в служебную беседу.\n\nВК говорит: {error_text}",
                    disable_mentions=1,
                )
                return True
            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"подал(-а) предложение по улучшению с содержанием: «{offer}»")            
            await message.reply("Спасибо за предложение по улучшению бота! Мы обязательно рассмотрим ваше предложение.")
            return True

        
        if command in ['addblack', 'блеклист', 'чс', 'blackadd', 'addch']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            
            target = int
            arg = 0
            if message.reply_message:
                target = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            sql.execute("SELECT * FROM blacklist WHERE user_id = ?", (target,))
            if sql.fetchone():
                await message.reply("Данный пользователь уже находится в черном списке бота!", disable_mentions=1)
                return True

            if await equals_roles(user_id, target, chat_id, message) < 2:
                await message.reply("Вы не можете добавить данного пользователя в ЧС!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            sql.execute("INSERT INTO blacklist (user_id, moderator_id, reason_gban, datetime_globalban) VALUES (?, ?, ?, ?)",
                        (target, user_id, reason, date_now))
            database.commit()

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) добавил @id{target} ({await get_user_name(target, chat_id)}) в черный список бота", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"добавил @id{target} (пользователя) в Чёрный список. Причина: {reason}")            
            return True


        
        if command in ['unblack', 'убратьчс', 'blackdel', 'unch']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            target = int
            if message.reply_message:
                target = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            sql.execute("SELECT * FROM blacklist WHERE user_id = ?", (target,))
            if not sql.fetchone():
                await message.reply("Данный пользователь не находится в черном списке бота!", disable_mentions=1)
                return True

            sql.execute("DELETE FROM blacklist WHERE user_id = ?", (target,))
            database.commit()

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) удалил @id{target} ({await get_user_name(target, chat_id)}) из черного списка бота!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"удалил @id{target} (пользователя) из Чёрного списка")            
            return True           
                
        if command in ['млоги', 'mlogs', 'смлоги', 'smlogs']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            is_network = command in ['смлоги', 'smlogs']
            target = None
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])

            if is_network:
                scope_chat_ids = await get_gsync_chats(chat_id)
                if not scope_chat_ids:
                    await message.reply("Эта беседа не подключена к сетке бесед.", disable_mentions=1)
                    return True
            else:
                scope_chat_ids = [chat_id]

            try:
                logs = await _read_moderation_logs(scope_chat_ids, target)
            except Exception as error:
                print(f"[MODERATION LOGS COMMAND ERROR] command={command} user={user_id} chat={chat_id}: {error}")
                await message.reply(
                    "Не удалось прочитать модерационные логи. База данных временно занята — повторите команду через несколько секунд.",
                    disable_mentions=1,
                )
                return True
            if not logs:
                old_count = await db_execute(
                    "SELECT COUNT(*) FROM logchats WHERE chat_id IS NULL", fetch='one'
                )
                legacy_hint = "\n\nСтарые записи без ID беседы не отображаются." if old_count and old_count[0] else ""
                scope_name = "сетке бесед" if is_network else "этой беседе"
                await message.reply(
                    f"Модерационные логи в {scope_name} отсутствуют.{legacy_hint}",
                    disable_mentions=1,
                )
                return True

            formatted, page, max_page = await _format_moderation_log_page(
                logs, 1, chat_id, show_chat=is_network
            )
            base_callback = "smlogs" if is_network else "mlogs"
            payload_common = {"page": page, "chatId": chat_id}
            if target is not None:
                payload_common["target"] = int(target)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": f"{base_callback}_minus", **payload_common}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("⏩", {"command": f"{base_callback}_plus", **payload_common}), color=KeyboardButtonColor.POSITIVE)
            )
            title = "Модерационные логи сетки бесед" if is_network else "Модерационные логи беседы"
            if target is not None:
                title += f" пользователя @id{target}"
            await message.reply(
                f"{title} [{page}/{max_page}]:\n\n" + "\n\n".join(formatted),
                disable_mentions=1, keyboard=keyboard
            )
            return True

        if command in ['логиобщие', 'logs', 'logsmoders', 'логи']:
            if not await _can_use_command(user_id, chat_id, 9):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target = None
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])

            if target:
                sql.execute("SELECT * FROM logchats WHERE user_id = ? ORDER BY rowid DESC LIMIT 9999999999999", (target,))
                logs = sql.fetchall()

                if not logs:
                    await message.reply(f"У @id{target} ({await get_user_name(target, chat_id)}) отсутствуют записи в логах модерации.", disable_mentions=1)
                    return True

                total = len(logs)
                per_page = MAX_LOGS
                max_page = (total + per_page - 1) // per_page

                async def get_moders_page(page: int):
                    start = (page - 1) * per_page
                    end = start + per_page
                    selected = logs[start:end]
                    formatted = []
                    for idx, entry in enumerate(selected, start=start + 1):
                        u_id, t_id, amount, log_text, *rest = entry

                        try:
                            u_info = await bot.api.users.get(user_ids=u_id)
                            u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                        except:
                            u_name = str(u_id)

                        if t_id:
                            try:
                                t_info = await bot.api.users.get(user_ids=t_id)
                                t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                                t_display = f"@id{t_id} ({t_name})"
                            except:
                                t_display = f"@id{t_id}"
                        else:
                            t_display = "None"

                        created_at = rest[0] if rest else "Дата не записана"
                        entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                        try:
                            a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                        except Exception:
                            a_display = "Пользователь"
                        l_display = log_text if log_text else "—"

                        formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
                    return formatted

                page = 1
                moders_page = await get_moders_page(page)
                moders_text = "\n\n".join(moders_page)

                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("⏪", {"command": "modersMinus", "target": target, "page": 1}), color=KeyboardButtonColor.NEGATIVE)
                    .add(Callback("⏩", {"command": "modersPlus", "target": target, "page": 1}), color=KeyboardButtonColor.POSITIVE)
                )

                await message.reply(
                    f"Логи модерации @id{target} ({await get_user_name(target, chat_id)}) [1/{max_page}]:\n\n{moders_text}",
                    disable_mentions=1, keyboard=keyboard
                )
                return True

            else:
                sql.execute("SELECT * FROM logchats ORDER BY rowid DESC LIMIT 9999999999999")
                logs = sql.fetchall()

                if not logs:
                    await message.reply(f"Логи с действиями модераторов отсутствуют!", disable_mentions=1)
                    return True

                total = len(logs)
                per_page = MAX_LOGS
                max_page = (total + per_page - 1) // per_page

                async def get_moders_page(page: int):
                    start = (page - 1) * per_page
                    end = start + per_page
                    selected = logs[start:end]
                    formatted = []
                    for idx, entry in enumerate(selected, start=start + 1):
                        u_id, t_id, amount, log_text, *rest = entry

                        try:
                            u_info = await bot.api.users.get(user_ids=u_id)
                            u_name = f"{u_info[0].first_name} {u_info[0].last_name}"
                        except:
                            u_name = str(u_id)

                        if t_id:
                            try:
                                t_info = await bot.api.users.get(user_ids=t_id)
                                t_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                                t_display = f"@id{t_id} ({t_name})"
                            except:
                                t_display = f"@id{t_id}"
                        else:
                            t_display = "None"

                        created_at = rest[0] if rest else "Дата не записана"
                        entry_chat_id = rest[1] if len(rest) > 1 and rest[1] else chat_id
                        try:
                            a_display = await resolve_log_role_display(int(u_id), int(entry_chat_id), amount)
                        except Exception:
                            a_display = "Пользователь"
                        l_display = log_text if log_text else "—"

                        formatted.append(f"{idx}. @id{u_id} ({u_name}) | Кому: {t_display} | Роль: {a_display or 'Пользователь'} | Дата: {created_at} | Лог: {l_display}")
                    return formatted

                page = 1
                moders_page = await get_moders_page(page)
                moders_text = "\n\n".join(moders_page)

                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("⏪", {"command": "modersAllMinus", "page": 1}), color=KeyboardButtonColor.NEGATIVE)
                    .add(Callback("⏩", {"command": "modersAllPlus", "page": 1}), color=KeyboardButtonColor.POSITIVE)
                )

                await message.reply(
                    f"Общие логи модерации [1/{max_page}]:\n\n{moders_text}",
                    disable_mentions=1, keyboard=keyboard
                )
                return True
                            
        if command in ["bug", "баг"]:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True
        	
            try:
                user_info = await bot.api.users.get(user_ids=user_id)
                full_name = f"{user_info[0].first_name} {user_info[0].last_name}"
            except:
                full_name = f"id{user_id} (Ошибка)"

            args = message.text.split(maxsplit=1)
            offer = args[1].strip() if len(args) > 1 else ""
            if len(arguments) < 2 or len(offer) < 5:
                await message.replyLocalizedMessage('command_bug_min_params')
                return True

            ok, error_text = await send_staff_feedback_message(
                f"👾 | Баг-трекер:\n"
                f"1. Пользователь: [id{user_id}|{full_name}]\n"
                f"2. Содержимое бага: {offer}\n"
                f"3. Беседа: ID {chat_id}",
                source_user_id=user_id,
                feedback_type="bug",
            )
            if not ok:
                await message.reply(
                    f"⚠️ Не удалось отправить баг-репорт в служебную беседу.\n\nВК говорит: {error_text}",
                    disable_mentions=1,
                )
                return True
            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"подал(-а) баг-репорт с содержанием: «{offer}»")            
            await message.replyLocalizedMessage('command_bug')
            return True           

        if command in ['stats', 'стата', 'статистика', 'stata', 'statistic']:
                
                user = int
                if message.reply_message:
                    user = message.reply_message.from_id
                elif len(arguments) >= 2 and await getID(arguments[1]):
                    user = await getID(arguments[1])
                else:
                    user = user_id

                if user < 0:
                    await message.reply("Нельзя взаимодействовать с сообществом!")
                    return True

                reg_data = "-"  
                role = await get_role(user, chat_id)
                warns = await get_warns(user, chat_id)

                
                if await is_nick(user, chat_id):
                    nick = await get_user_name(user, chat_id)
                else:
                    nick = "Нет"

                
                try:
                    info = await bot.api.users.get(user_ids=user)
                    name = f"{info[0].first_name} {info[0].last_name}"
                except:
                    name = f"@id{user} (Не удалось получить имя)"

                messages = await message_stats(user, chat_id)
                msg = await messageslist(user, chat_id)
                msg_do = []
                ms = 0
                for i in msg:
                   ms = ms + 1
                   if ms <= 10: 
                   	msg_do.append(i)
                msg_str = "\n".join(msg_do)
                                                                                 
                
                sql.execute("SELECT * FROM gbanlist WHERE user_id = ?", (user,))
                gban = sql.fetchone()
                gban_status = "Да" if gban else "Нет"

                
                sql.execute("SELECT * FROM globalban WHERE user_id = ?", (user,))
                gban2 = sql.fetchone()
                globalban = "Да" if gban2 else "Нет"

                
                sql.execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user,))
                mute = sql.fetchone()
                mute_status = "Да" if mute else "Нет"

                
                sql.execute("SELECT chat_id FROM chats")
                chats_list = sql.fetchall()
                bans = ""
                bans_count = 0
                i = 1
                for c in chats_list:
                    chat_id_check = c[0]
                    try:
                        sql.execute(f"SELECT moder, reason, date FROM bans_{chat_id_check} WHERE user_id = ?", (user,))
                        user_bans = sql.fetchall()
                        if user_bans:
                            bans_count += len(user_bans)
                            for ub in user_bans:
                                mod, reason, date = ub
                                bans += f"{i}) @id{mod} (Модератор) | {reason} | {date} МСК (UTC+3)\n"
                                i += 1
                    except:
                        continue  

                role_display = await get_role_display_name(user, chat_id)

                
                keyboard = None
                if await _has_role_level(user_id, chat_id, 2):
                    keyboard = Keyboard(inline=True)
                    keyboard.add(
                        Callback("Все предупреждения", {"command": "activeWarns", "user": user, "chatId": chat_id}),
                        color=KeyboardButtonColor.PRIMARY
                    )
                    keyboard.add(
                        Callback("Информация о блокировках", {"command": "getban", "user": user, "chatId": chat_id}),
                        color=KeyboardButtonColor.PRIMARY
                    )

                await message.replyLocalizedMessage('command_stats', {
                        'user': f'@id{user} (пользователе)',
                        'role': role_display,
                        'bans': bans_count,
                        'gban': globalban,
                        'gbanpl': gban_status,
                        'warns': warns,
                        'mute': mute_status,
                        'nickname': nick,
                        'messages': messages['count'],
                        'last_message': messages['last'],                       
                    }, keyboard=keyboard)
                return True

        is_company_registration = (
            command == 'регистрация'
            and len(arguments) > 1
            and str(arguments[1]).strip().lower().replace('o', 'о') == 'ооо'
        )

        if command in ['reg', 'рег', 'registration', 'regdate', 'регистрация', 'датарегистрации'] and not is_company_registration:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target = user_id
            if message.reply_message and message.reply_message.from_id > 0:
                target = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = int(await getID(arguments[1]))

            if int(target) < 0:
                await message.reply("Нельзя проверить дату регистрации сообщества.", disable_mentions=1)
                return True

            try:
                reg_info = await fetch_regvk_registration(int(target))
                target_name = await get_user_name(int(target), chat_id)
                await message.reply(
                    f"Дата регистрации [id{target}|{target_name}]: {reg_info['date']}\n"
                    f"Прошло времени: {reg_info['elapsed']}\n"
                    f"Адрес страницы: {reg_info['url']}",
                    disable_mentions=1,
                )
            except Exception as error:
                await message.reply(
                    f"Не удалось получить дату регистрации через regvk.com: {str(error)[:160]}",
                    disable_mentions=1,
                )
            return True

        if command in ['топсмс', 'topsms', 'topmsg', 'топсообщений']:
            try:
                rows = sql.execute(
                    f"SELECT user_id, COUNT(*) AS msg_count FROM messages_{chat_id} "
                    "WHERE user_id > 0 GROUP BY user_id ORDER BY msg_count DESC LIMIT 10"
                ).fetchall()

                if not rows:
                    await message.reply("В этой беседе пока нет статистики сообщений.", disable_mentions=1)
                    return True

                lines = ["📊 Топ 10 самых активных пользователей в беседе:", ""]
                for index, (target_user_id, msg_count) in enumerate(rows, start=1):
                    user_name = await get_user_name(int(target_user_id), chat_id)
                    lines.append(f"Топ {index}. [id{target_user_id}|{user_name}] | {format_number(int(msg_count))} смс")

                await message.reply("\n".join(lines), disable_mentions=1)
            except Exception as error:
                await message.reply(f"Не удалось получить топ сообщений: {str(error)[:200]}", disable_mentions=1)
            return True

        if command in ['blacklist']:
            if not await _can_use_command(user_id, chat_id, 8):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            page = 1
            if len(arguments) >= 2 and arguments[-1].isdigit():
                page = int(arguments[-1])

            try:
                sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM blacklist ORDER BY rowid DESC")
                rows = sql.fetchall()
            except Exception as e:
                print(f"[blacklist] DB error: {e}")
                rows = []

            total_items = len(rows)
            page_items, total_pages = paginate_list(rows, page, 20)

            if total_pages > 0 and page > total_pages:
                await message.reply(
                    f"Последняя страница чёрного списка: {total_pages}.",
                    disable_mentions=1,
                )
                return True

            if not page_items:
                await message.reply("Чёрный список пуст.", disable_mentions=1)
                return True

            text = ""
            i = (page-1)*20 + 1
            for r in page_items:
                uid, mod, reason, dt = r
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mod, chat_id)
                except:
                    mod_name = str(mod)
                text += f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mod}|Модератор] | Причина: {reason} | {dt}\n"
                i += 1

            kb = make_nav_keyboard("blacklist", page, chat_id)
            await message.reply(f"Список пользователей в черном списке бота (страница {page}/{total_pages}):\n\n{text}", disable_mentions=1, keyboard=kb)
            return True

        if command in ['gbanlist']:
            if not await _can_use_command(user_id, chat_id, 8):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            page = 1
            if len(arguments) >= 2 and arguments[-1].isdigit():
                page = int(arguments[-1])

            rows = []
            try:
                sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM gbanlist ORDER BY rowid DESC")
                rows += sql.fetchall()
            except Exception as e:
                print(f"[gbanlist] gbanlist read error: {e}")
            try:
                sql.execute("SELECT user_id, moderator_id, reason_gban, datetime_globalban FROM globalban ORDER BY rowid DESC")
                rows += sql.fetchall()
            except Exception as e:
                print(f"[gbanlist] globalban read error: {e}")

            total_items = len(rows)
            page_items, total_pages = paginate_list(rows, page, 20)

            if total_pages > 0 and page > total_pages:
                await message.reply(
                    f"Последняя страница глобальной блокировки: {total_pages}.",
                    disable_mentions=1,
                )
                return True

            if not page_items:
                await message.reply("Список пользователей в глобальной блокировке отсутствует!", disable_mentions=1)
                return True

            text = ""
            i = (page-1)*20 + 1
            for r in page_items:
                uid, mod, reason, dt = r
                try:
                    user_name = await get_user_name(uid, chat_id)
                except:
                    user_name = str(uid)
                try:
                    mod_name = await get_user_name(mod, chat_id)
                except:
                    mod_name = str(mod)
                text += f"{i}. {format_vk_profile_link(uid, user_name)} | [id{mod}|Модератор] | Причина: {reason} | {dt}\n"
                i += 1

            kb = make_nav_keyboard("gbanlist", page, chat_id)
            await message.reply(f"Список пользователей в глобальной блокировке (страница {page}/{total_pages}):\n\n{text}", disable_mentions=1, keyboard=kb)
            return True
                         
        if command in ["banid", "банчата"]:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            if len(arguments) < 2:
                await message.reply("Укажите чат!")
                return True

            try:
                target_chat = int(arguments[1])
            except:
                await message.reply("Укажите чат!")
                return True

            sql.execute("SELECT chat_id FROM banschats WHERE chat_id = ?", (target_chat,))
            if sql.fetchone():
                await message.reply("Беседа уже находится в блокировке!")
                return True

            sql.execute("INSERT INTO banschats (chat_id) VALUES (?)", (target_chat,))
            database.commit()
            
            target_peer = 2000000000 + target_chat
            await bot.api.messages.send(
                peer_id=target_peer,
                random_id=0,
                message=(
                    f"Владелец беседы — не член, ADMIN MANAGER! Я не буду здесь работать."
                )
            )

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) беседу №«{target_chat}»")
            return True

        if command in ["unbanid", "разбанчата"]:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            if len(arguments) < 2:
                await message.reply("Укажите чат!")
                return True

            try:
                target_chat = int(arguments[-1])
            except:
                await message.reply("Укажите чат!")
                return True

            sql.execute("SELECT chat_id FROM banschats WHERE chat_id = ?", (target_chat,))
            if not sql.fetchone():
                await message.reply("Беседа и так находится в блокировке!")
                return True

            sql.execute("DELETE FROM banschats WHERE chat_id = ?", (target_chat,))
            database.commit()
            
            target_peer = 2000000000 + target_chat
            await bot.api.messages.send(
                peer_id=target_peer,
                random_id=0,
                message=(
                    f"Чат разблокирован в боте!"
                )
            )

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) беседу №«{target_chat}»")
            return True
            
        if command in ["clearchat", "удалитьчат", "delchat"]:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            if len(arguments) < 2:
                await message.reply("Укажите чат!")
                return True

            try:
                target_chat = int(arguments[-1])
            except:
                await message.reply("Укажите чат!")
                return True
                
            target_peer = 2000000000 + target_chat
            try:
                await bot.api.messages.send(
                    peer_id=target_peer,
                    random_id=0,
                    message="Чат удален из базы данных бота! Работа бота в чате полностью прекращена."
                )
            except Exception as error:
                print(f"[delchat] Не удалось отправить уведомление в чат {target_chat}: {error}")

            deleted_tables = await delete_chat_from_database(target_chat, stop_chat=True)

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) удалил(-а) беседу №«{target_chat}» из базы данных "
                f"и полностью остановил(-а) её работу в боте. Очищено таблиц: {deleted_tables}.",
                disable_mentions=1
            )
            return True
                        
        if command in ['help', 'помощь', 'хелп', 'команды', 'commands']:
            commands_levels = {
                0: [
                    'Команды пользователя:',
                    '/info -- офицальные ресурсы проекта',
                    '/правила — правила чата установленные владельцем беседы',
                    '/правилабота -- глобальные правила проекта и основания для глобальных блокировок',
                    '/infobot — офицальные ресурсы бота',
                    '/stats -- информация о пользователе',
                    '/топсмс -- топ пользователей по количеству сообщений',
                    '+rep (+реп) — повысить репутацию пользователю',
                    '-rep (-реп) — понизить репутацию пользователю',
                    '/реп — посмотреть свою репутацию',
                    '/топреп — глобальный топ положительной репутации',
                    '/getid -- узнать оригинальный ID пользователя в ВК',
                    '/q -- выход из текущей беседы',
                    '/командыстраны -- все команды, связанные со странами',
                    '/карта -- динамическая карта мира по странам',
                    '/ивент -- активные временные ивенты',
                    '/form -- подать форму на бан (в определенном чате)',
                    '/offer -- предложить улучшение для бота'
                ],
                1: [
                    '\nКоманды модератора:',
                    '/setnick — сменить ник у пользователя',
                    '/removenick — очистить ник у пользователя',
                    '/getnick — проверить ник пользователя',
                    '/getacc — узнать пользователя по нику',
                    '/nlist — просмотреть ники пользователей',
                    '/nonick — пользователи без ников',
                    '/kick — исключить пользователя из беседы',
                    '/warn — выдать предупреждение пользователю',
                    '/unwarn — снять предупреждение пользователю',
                    '/getwarn — информация о активных предупреждениях пользователя',
                    '/warnhistory — информация о всех предупреждениях пользователя',
                    '/warnlist — список пользователей с варном',
                    '/staff — пользователи с ролями',
                    '/reg -- дата регистрации страницы ВКонтакте',
                    '/mute — замутить пользователя',
                    '/unmute — размутить пользователя',
                    '/alt — узнать альтернативные команды',
                    '/getmute -- информация о муте пользователя',
                    '/mutelist -- список пользователей с мутом',
                    '/clear -- очистить сообщения',
                    '/getban -- информация о банах пользователя',
                    '/delete -- удалить сообщение пользователя',
                    '/chatid -- узнать оригинальный айди чата в боте'
                ],
                2: [
                    '\nКоманды старшего модератора:',
                    '/ban — заблокировать пользователя в беседе',
                    '/unban -- разблокировать пользователя в беседе',
                    '/addmoder -- выдать пользователю модератора',
                    '/removerole -- забрать роль у пользователя',
                    '/online -- упомянуть пользователей онлайн',
                    '/onlinelist — посмотреть пользователей в онлайн',
                    '/banlist -- посмотреть заблокированных',
                    '/inactivelist -- список неактивных пользователей'
                ],
                3: [
                    '\nСписок команд администратора:',
                    '/quiet -- Включить выключить режим тишины',
                    '/skick -- исключить пользователя с бесед сетки',
                    '/sban -- заблокировать пользователя в сетке бесед',
                    '/sunban — разбанить пользователя в сетке бесед',
                    '/addsenmoder — выдать права старшего модератора',
                    '/rnickall -- очистить все ники в беседе',
                    '/sremovenick -- очистить ник у пользователя в сетке бесед',
                    '/zov -- упомянуть всех пользователей',
                    '/srole -- выдать права в сетке бесед',
                    '/млоги (/mlogs) -- модерационные логи текущей беседы',
                    '/смлоги (/smlogs) -- модерационные логи сетки бесед'
                ],
                4: [
                    '\nСписок команд старшего администратора:',
                    '/addadmin -- выдать права администратора',
                    '/serverinfo -- информация о сервере',
                    '/filter -- фильтр запрещенных слов',
                    '/sremoverole -- забрать роль у пользователя в сетке бесед',
                    '/ssetnick -- установить ник в сетке бесед',
                    '/bug -- отправить баг-трекер разработчику бота',
                    '/report -- жалоба на пользователя'
                    '/szov -- вызов участников бесед сетки',
                ],
                5: [
                    '\nСписок команд зам. спец администратора:',
                    '/addsenadmin -- выдать права старшего администратора',
                    '/sync -- синхронизация с базой данных',
                    '/pin -- закрепить сообщение',
                    '/unpin -- открепить сообщение',
                    '/deleteall -- удалить последние 200 сообщений пользователя',
                    '/gsinfo -- информация о глобальной привязке',
                    '/gsrnick -- очистить ник у пользователя в беседах привязки',
                    '/gssnick -- поставить ник пользователю в беседах привязки',
                    '/gskick -- исключить пользователя с бесед привязки',
                    '/gsban -- заблокировать пользователя в беседах привязки',
                    '/gsunban -- разбанить пользователя в беседах привязки'
                ],
                6: [
                    '\nСписок команд спец. администратора:',
                    '/addzsa -- выдать права зам. спец. администратора',
                    '/server -- привязать беседу к серверу',
                    '/settings -- показать настройки беседы',
                    '/clearwarn -- снять варны всем пользователям',
                    '/title -- изменить название беседы',
                    '/antisliv -- включить систему антислива в беседе'
                ],
                7: [
                    '\nСписок команд владельца беседы:',
                    '/addsa -- выдать права спец. администратора',
                    '/antiflood -- режим защиты от спама',
                    '/welcometext -- текст приветствия',
                    '/invite -- система добавления пользователей только модераторами',
                    '/leave -- система исключения пользователей при выходе',
                    '/editowner -- передать права владельца беседы',
                    '/masskick -- исключить участников без ролей',
                    '/защита -- защита от сторонних сообществ',
                    '/settingsmute -- включить выдачу варнов за написание сообщений в муте',
                    '/setinfo -- установить информацию о официальных ресурсах проекта в «/info»',
                    '/setrules -- установить правила беседы в «/rules»',
                    '/type – изменить тип беседы',
                    '/gsync -- поставить глобальную синхронизацию бесед',
                    '/gunsync – отключить глобальную синхронизацию бесед',
                    '/masskick -- исключить нескольких пользователей',
                    '/amnesty -- амнистия наказаний в чате',
                    '/settingsgame -- запретить игры в беседе',
                    '/settingsphoto -- запретить отправку фото в беседу',
                    '/gzovoff -- отключить глобальную рассылку в чат',
                    '/gzovon -- включить глобальную рассылку в чат'
                ],
                8: [
                    '\nСписок команд Медиа Партнера:',
                    '/gbanlist -- список пользователей в глобальной блокировке',
                    '/blacklist -- список пользователей в черном списке бота'
                ],
                9: [
                    '\nСписок команд технического специалиста:',
                    '/логи -- логи модераторских действий (пользователя или общие)',
                ],
                10: [
                    '\nСписок команд заместителя директора:',
                    '/gbanpl -- заблокировать пользователя во всех беседах игроков',
                    '/gunbanpl -- разбанить пользователя во всех беседах игроков',
                    '/gban -- заблокировать пользователя в чатах',
                    '/ungban -- разблокировать пользователя в чатах',
                    '/логи -- логи модераторских действий (пользователя или общие)',
                    '/gbanlist -- список пользователей в глобальной блокировке',
                    '/blacklist -- список пользователей в черном списке бота'
                ],
                11: [
                    '\nСписок команд осн. заместителя директора:',
                    '/setowner – установить владельца беседы',
                    '/gstaff – пользователи с глобальными ролями',
                    '/grrole -- забрать роль (глобальную)'
                ],
                12: [
                    f'\nСписок команд {GLOBAL_TECH_CURATOR_NAME}:',
                    '/addtechspec -- выдать права технического специалиста'
                ],
                13: [
                    '\nСписок команд Зам. Главного Тех. Специалиста:',
                    '/addtechcurator -- выдать права Куратора Технических специалистов',
                    '/astats -- статистика администратора',
                    '/norm -- норма администратора',
                    '/epoints -- магазин Epoints'
                ],
                14: [
                    '\nСписок команд Главного Тех. Специалиста:',
                    '/addtechcurator -- выдать права Куратора Технических специалистов',
                    '/astats -- статистика администратора',
                    '/norm -- норма администратора',
                    '/epoints -- магазин Epoints'
                ],
                15: [
                    '\nСписок команд директора бота:',
                    '/addtechcurator -- выдать права Куратора Технических специалистов',
                    '/createpromo -- создать промо-код',
                    '/delpromo -- удалить промо-код',
                    '/listpromo -- список промо-кодов',
                    '/addblack -- добавить пользователя в черный список бота',
                    '/unblack -- убрать пользователя из черного списка бота',
                    '/astats -- статистика администратора',
                    '/norm -- норма администратора',
                    '/epoints -- магазин Epoints'
                ],
                16: [
                    f'\nСписок команд {GLOBAL_QUALITY_CONTROL_NAME}:',
                    '/кк -- выдать себе роль Контроля качества',
                    '/оффроль -- снять с себя глобальную роль',
                    '/restart -- перезапустить бота',
                    '/раздача -- раздача монет пользователям',
                    '/addgtech -- выдать права Главного Тех. Специалиста',
                    '/addzgtech -- выдать права Зам. Главного Тех. Специалиста',
                    '/adddirector -- выдать права директора бота',
                ],
                17: [
                    '\nСписок команд разработчика бота:',
                    '/addkk — выдать роль Контроля качества',
                    '/createpromo -- создать промо-код',
                    '/delpromo -- удалить промо-код',
                    '/listpromo -- список промо-кодов',
                    '/adddev -- выдать права разработчика бота',
                    '/чекбд -- проверить базу данных',
                    '/фиксбд -- восстановить/проверить таблицы базы данных',
                ]
            }

            if await _is_modified_chat(chat_id):
                sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
                database.commit()
                custom_roles_fetch = await db_execute(
                    f"SELECT level, name FROM custom_roles_{chat_id}", fetch='all'
                )
                custom_roles = sorted(
                    [(int(row[0]), row[1]) for row in (custom_roles_fetch or []) if 1 <= int(row[0]) <= 100],
                    key=lambda row: row[0]
                )
                user_role = await get_role(user_id, chat_id)
                user_perm = MODIFIED_ROLE_COMMAND_THRESHOLDS[7] if _is_encoded_global_role(user_role) else _modified_real_role_level(user_role)

                commands = []
                for b in commands_levels[0]:
                    commands.append(b)

                local_role_command_prefixes = (
                    '/addmoder', '/removerole', '/addsenmoder', '/srole',
                    '/addadmin', '/sremoverole', '/addsenadmin', '/addzsa', '/addsa'
                )

                role_groups = {}
                group_order = []
                for perm_level, role_name in custom_roles:
                    if user_perm < perm_level:
                        continue
                    std_level = _modified_standard_role_level(perm_level)
                    if std_level <= 0 or std_level not in commands_levels:
                        continue
                    if std_level not in role_groups:
                        role_groups[std_level] = []
                        group_order.append(std_level)
                    role_groups[std_level].append((perm_level, role_name))

                roles_command_added = False
                for std_level in group_order:
                    role_labels = ', '.join(
                        f"{role_name} - {perm_level}"
                        for perm_level, role_name in role_groups[std_level]
                    )
                    role_commands = [
                        b for b in commands_levels[std_level]
                        if not any(b.lstrip().startswith(prefix) for prefix in local_role_command_prefixes)
                    ]
                    if not roles_command_added:
                        role_commands.append('/роли -- список ролей модифицированного чата')
                        roles_command_added = True
                    if std_level == 2:
                        role_commands.extend([
                            '/unrole -- снять кастомную роль у пользователя',
                            '/unsmsrole -- снять кастомную роль в сетке бесед'
                        ])
                    elif std_level == 3:
                        role_commands.extend([
                            '/role -- выдать кастомную роль пользователю',
                            '/smsrole -- выдать кастомную роль в сетке бесед'
                        ])
                    elif std_level == 7:
                        role_commands.extend([
                            '/unmrole -- отключить модифицированные роли',
                            '/createrole -- создать кастомную роль',
                            '/delrole -- удалить кастомную роль',
                            '/rename -- переименовать кастомную роль',
                            '/rerole -- изменить уровень кастомной роли',
                            '/smrole -- включить модифицированные роли в сетке бесед'
                        ])
                    for b in role_commands:
                        if b.startswith('\n'):
                            commands.append(f'\nКоманды ({role_labels}):')
                        else:
                            commands.append(b)

                if _is_encoded_global_role(user_role):
                    for std_level in sorted(level for level in commands_levels.keys() if level >= 8):
                        if _role_can_satisfy_standard_min(user_role, std_level):
                            for b in commands_levels[std_level]:
                                commands.append(b)

                keyboard = None
                if _role_can_satisfy_standard_min(user_role, 2):
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Альтернативные команды", {"command": "alt", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                    )

                level_commands = '\n'.join(commands)
                await message.reply(f"{level_commands}", disable_mentions=1, keyboard=keyboard)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список доступных команд")
            else:
                user_role = await get_role(user_id, chat_id)

                if _role_can_satisfy_standard_min(user_role, 2):
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Альтернативные команды", {"command": "alt", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                    )
                else:
                    keyboard = None

                commands = []
                for i in commands_levels.keys():
                    if _role_can_satisfy_standard_min(user_role, i):
                        command_items = list(commands_levels[i])
                        if i == 7:
                            command_items.append('/mrole -- включить модифицированные роли')
                        for b in command_items:
                            commands.append(b)

                level_commands = '\n'.join(commands)
                await message.reply(f"{level_commands}", disable_mentions=1, keyboard=keyboard)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список доступных команд")            

        if command in ['snick', 'setnick', 'nick', 'addnick', 'ник', 'сетник', 'аддник']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if user != user_id and await equals_roles(user_id, user, chat_id, message) == 0:
                await message.replyLocalizedMessage('command_setnick_preminissions')
                return True

            new_nick = await get_string(arguments, arg)
            if not new_nick:
                await message.replyLocalizedMessage('command_setnick_nick')
                return True
            else: await setnick(user, chat_id, new_nick)

            await message.replyLocalizedMessage('command_setnick', {
                        'user': userf,
                        'target': f'@id{user} (пользователю)',
                        'nick': new_nick
                    })
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"установил(-а) новый ник @id{user} (пользователю). Новый ник: {new_nick}")                       

        if command in ['rnick', 'removenick', 'clearnick', 'cnick', 'рник', 'удалитьник', 'снятьник']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) == 0:
                await message.replyLocalizedMessage('command_removenick_premminisions')
                return True

            await rnick(user, chat_id)
            await message.replyLocalizedMessage('command_removenick', {
                        'user': userf,
                        'target': f'@id{user} (пользователю)'
                    })
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"удалил(-а) старый ник @id{user} (пользователю)")            

        if command in ['type', 'тип']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            if len(arguments) < 2:
                
                sql.execute(f"SELECT type FROM chats WHERE chat_id = {chat_id}")
                current_type = sql.fetchone()
                if current_type:
                    type_value = current_type[0]
                    await message.reply(
                        f"Беседа имеет тип: {chat_types.get(type_value, type_value)}\n\n"
                        "Все типы бесед:\n" +
                        "\n".join([f"{k} -- {v}" for k, v in chat_types.items()]),
                        disable_mentions=1
                    )
                return True

            new_type = arguments[1].lower()

            
            if new_type not in chat_types:
                await message.reply(
                    "Неверный тип беседы, типы:\n" +
                    "\n".join([f"{k} -- {v}" for k, v in chat_types.items()]),
                    disable_mentions=1
                )
                return True

            
            sql.execute(f"UPDATE chats SET type = ? WHERE chat_id = ?", (new_type, chat_id))
            database.commit()

            await message.replyLocalizedMessage('command_settype', {
                        'type': chat_types[new_type]
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"установил(-а) новый тип беседы. Новый тип: {chat_types[new_type]}")            
            
        if command in ["settings", "настройки"]:
            if not await _can_use_command(user_id, chat_id, 6):
                await message.replyLocalizedMessage('not_preminisionss')
                return

            
            x = await bot.api.messages.get_conversations_by_id(
                peer_ids=peer_id,
                extended=1,
                fields='chat_settings',
                group_id=message.group_id
            )
            x = json.loads(x.model_dump_json())
            chat_owner = None
            chat_title = None
            for i in x['items']:
                chat_owner = int(i["chat_settings"]["owner_id"])
                chat_title = i["chat_settings"]["title"]

            
            sql.execute(f"SELECT type, in_pull, filter, leave_kick, invite_kick, antiflood FROM chats WHERE chat_id = {chat_id}")
            row = sql.fetchone()
            if row:
                type_value = chat_types.get(row[0], row[0])
                server = await get_current_server(chat_id)
                filter_text = "Включено" if row[2] == 1 else "Выключено"
                leave_text = "Включено" if row[3] == 1 else "Выключено"
                invite_text = "Включено" if row[4] == 1 else "Выключено"
                antiflood_text = "Включено" if row[5] == 1 else "Выключено"
            else:
                type_value = "Общие беседы"
                server = "0"
                filter_text = "Выключено"
                leave_text = "Выключено"
                invite_text = "Выключено"
                antiflood_text = "Выключено"

            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) текущие настройки беседы")            
            await message.replyLocalizedMessage('command_settings', {
                        'chat_title': chat_title,
                        'owner': f'@id{chat_owner} ({await get_user_name(chat_owner, chat_id)})',
                        'type': type_value,
                        'chat_id': chat_id,
                        'filter': filter_text,
                        'leave': leave_text,
                        'antiflood': antiflood_text,
                        'invite': invite_text,
                        'server': server                                                                                               
                    })            
            return            

        if command in ['gsrnick', 'грник']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_chats = await get_gsync_chats(chat_id)
            if not gsync_chats:
                await message.reply("Беседа не привязана к глобальной связке!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) == 0:
                await message.reply("Вы не можете снять ник у данного пользователя!", disable_mentions=1)
                return True

            for i in gsync_chats:
                try:
                    await rnick(user, i)
                except:
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал ник у @id{user} (пользователя) во всех беседах глобальной связки.", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял ник @id{user} (пользователю) во всех беседах глобальной связки")
            return True
            
        if command in ['gssnick', 'гссник']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_chats = await get_gsync_chats(chat_id)
            if not gsync_chats:
                await message.reply("Беседа не привязана к глобальной связке!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) == 0:
                await message.reply("Вы не можете установить ник данному пользователю!", disable_mentions=1)
                return True

            new_nick = await get_string(arguments, arg)
            if not new_nick:
                await message.reply("Укажите ник!", disable_mentions=1)
                return True

            for i in gsync_chats:
                try:
                    await setnick(user, i, new_nick)
                except:
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) установил ник @id{user} (пользователю) во всех беседах глобальной связки.\nНовый ник: {new_nick}", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"установил ник {new_nick} @id{user} (пользователю) во всех беседах глобальной связки")
            return True

        if command in ['settingsphoto', 'настройкифото']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            sql.execute("SELECT * FROM photosettings WHERE chat_id = ?", (chat_id,))
            row = sql.fetchone()
            if row is None:
                sql.execute("INSERT INTO photosettings (chat_id, mode) VALUES (?, ?)", (chat_id, 1))
                database.commit()
                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), включил(-а) систему удаления фотографий!", disable_mentions=1)
            else:
                new_mode = 0 if row[1] == 1 else 1
                sql.execute("UPDATE photosettings SET mode = ? WHERE chat_id = ?", (new_mode, chat_id))
                database.commit()
                if new_mode == 0:
                    await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), выключил(-а) систему удаления фотографий!", disable_mentions=1)
                else:
                    await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), включил(-а) систему удаления фотографий!", disable_mentions=1)

            return True            


        if command in ['gskick', 'гскик']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_chats = await get_gsync_chats(chat_id)
            if not gsync_chats:
                await message.reply("Беседа не привязана к глобальной связке!", disable_mentions=1)
                return True

            user = int
            reason = None
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            kicked_chats = 0
            failed_chats = 0
            for i in gsync_chats:
                try:
                    await bot.api.messages.remove_chat_user(i, user)
                    kicked_chats += 1
                    msg = f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил @id{user} ({await get_user_name(user, chat_id)}) в беседах глобальной связки!"
                    if reason:
                        msg += f"\nПричина: {reason}"
                    await bot.api.messages.send(peer_id=2000000000 + i, message=msg, disable_mentions=1, random_id=0)
                except Exception:
                    failed_chats += 1

            if kicked_chats <= 0:
                await message.reply(
                    f"Не удалось исключить @id{user} (пользователя) ни из одной беседы глобальной связки.",
                    disable_mentions=1,
                )
                return True

            fail_note = f"\nНе удалось исключить в {failed_chats} бесед(е/ах)." if failed_chats > 0 else ""
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил @id{user} (пользователя) "
                f"из {kicked_chats} бесед глобальной связки.{fail_note}",
                disable_mentions=1,
            )
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"исключил @id{user} из всех бесед глобальной связки")
            return True

        if command in ['gsban', 'гсбан']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_chats = await get_gsync_chats(chat_id)
            if not gsync_chats:
                await message.reply("Беседа не привязана к глобальной связке!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете заблокировать данного пользователя!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            for i in gsync_chats:
                try:
                    await ban(user, user_id, i, reason)
                    await bot.api.messages.remove_chat_user(i, user)
                    msg = f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил @id{user} ({await get_user_name(user, chat_id)}) в беседах глобальной связки!"
                    if reason:
                        msg += f"\nПричина: {reason}"
                    await bot.api.messages.send(peer_id=2000000000 + i, message=msg, disable_mentions=1, random_id=0)
                except:
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал @id{user} (пользователя) во всех беседах глобальной связки.\nПричина: {reason}", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"заблокировал @id{user} (пользователя) во всех беседах глобальной связки. Причина: {reason}")
            return True            
            
        if command in ['gsunban', 'гсунбан']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_chats = await get_gsync_chats(chat_id)
            if not gsync_chats:
                await message.reply("Беседа не привязана к глобальной связке!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) == 0:
                await message.reply("Вы не можете разбанить данного пользователя!", disable_mentions=1)
                return True

            for i in gsync_chats:
                try:
                    await unban(user, i)
                except:
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял блокировку с @id{user} (пользователя) во всех беседах глобальной связки.", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"разблокировал @id{user} во всех беседах глобальной связки")
            return True
            
        if command in ['getacc', 'acc', 'гетакк', 'аккаунт', 'account']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            nick = await get_string(arguments, 1)
            if not nick:
                await message.replyLocalizedMessage('command_getacc_params')
                return True

            nick_result = await get_acc(chat_id, nick)

            if not nick_result: await message.replyLocalizedMessage('command_getacc_not')
            else:
                info = await bot.api.users.get(nick_result)
                await message.reply(f"Ник {nick} принадлежит @id{nick_result} ({info[0].first_name} {info[0].last_name})", disable_mentions=1)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-a) кому принадлежит НикНейм «{nick}»")            

        if command in ['getnick', 'gnick', 'гник', 'гетник']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            nick = await get_nick(user, chat_id)
            if not nick: await message.replyLocalizedMessage('command_getnick_not')
            else: await message.reply(f"Ник данного @id{user} (пользователя): {nick}", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"посмотрел(-а) текущее имя @id{user} (пользователя). Текущий ник: «{nick}»")            

        if command in ['никлист', 'ники', 'всеники', 'nlist', 'nickslist', 'nicklist', 'nicks']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            nicks = await nlist(chat_id, 1)
            nick_list = '\n'.join(nicks)
            if nick_list == "": nick_list = "Ники отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: «/nonick»", disable_mentions=1, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) пользователей с ником")            

        if command in ['nonick', 'nonicks', 'nonicklist', 'nolist', 'nnlist', 'безников', 'ноникс']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            nonicks = await nonick(chat_id, 1)
            nonick_list = '\n'.join(nonicks)
            if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                     color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) пользователей без ников")            

        if command in ['kick', 'кик', 'исключить']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_kick_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, arg)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except:
                await message.replyLocalizedMessage('command_kick_not', {
                        'target': f'@id{user} (пользователя)'
                    })
                return True

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            if not reason:
                await message.replyLocalizedMessage('command_kick', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    }, keyboard=keyboard)
            else:
                await message.replyLocalizedMessage('command_kick_reason', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'reason': reason
                    }, keyboard=keyboard)           	
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"исключил(-а) @id{user} (пользователя) из беседы")            

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['warn', 'пред', 'варн', 'pred', 'предупреждение']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_warn_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.replyLocalizedMessage('command_warn_select_reason')
                return True

            warns = await warn(chat_id, user, user_id, reason)
            if warns < 3:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять варн", {"command": "unwarn", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
                )
                await message.replyLocalizedMessage('command_warn', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'warns': warns,
                        'reason': reason
                    })                
            else:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}),color=KeyboardButtonColor.NEGATIVE)
                )
                await message.replyLocalizedMessage('command_warn_end', {
                        'user': f'@id{user} (Пользователь)',
                        'reason': reason
                    })
                try: await bot.api.messages.remove_chat_user(chat_id, user)
                except Exception as e: print(f'Произошла ошибка при исключении: {user}', e)
                await clear_warns(chat_id, user)

            await add_punishment(chat_id, user_id)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) предупреждение @id{user} (пользователю). Причина: {reason}, Итого у пользователя: {warns}/3")            
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unwarn', 'унварн', 'анварн', 'снятьпред', 'минуспред']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_unwarn_preminisionss')
                return True

            if await get_warns(user, chat_id) < 1:
                await message.replyLocalizedMessage('command_warn_null', {
                        'user': f'@id{user} (пользователя)'
                    })
                return True

            warns = await unwarn(chat_id, user)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял(-а) предупреждение @id{user} (пользователю)")            
            await message.replyLocalizedMessage('command_unwarn', {            
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'warns': warns
                    })
            
        
        if command in ['rules', 'правила', 'правилачата']:
            sql.execute("SELECT description FROM rules WHERE chat_id = ?", (chat_id,))
            rules_text = sql.fetchone()

            if not rules_text:
                await message.replyLocalizedMessage('command_rules_not')
                return True

            await message.replyLocalizedMessage('command_rules', {
                        'rules': rules_text[0]
                    })
            return True

        if command in ['правилабота', 'botrules', 'rulesbot']:
            await message.reply(BOT_RULES_TEXT, disable_mentions=1)
            return True

        
        if command in ['setrules', 'установитьправила']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2:
                await message.replyLocalizedMessage('command_setrules_params')
                return True

            text = get_raw_text_tail(raw_text, 1)
            sql.execute("INSERT OR REPLACE INTO rules (chat_id, description) VALUES (?, ?)", (chat_id, text))
            database.commit()

            await message.replyLocalizedMessage('command_setrules', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'text': text
                    })            
            return True

        if command in ['infoid', 'инфоайди', 'чатыпользователя', 'инфоид']:
                if not await _has_role_level(user_id, chat_id, 11):
                        await message.replyLocalizedMessage('not_preminisionss')
                        return True

                if len(arguments) < 2:
                        await message.replyLocalizedMessage('select_user')
                        return True

                target = await getID(arguments[1])
                if not target:
                        await message.reply("Не удалось определить пользователя.", disable_mentions=1)
                        return True

                sql.execute("SELECT chat_id FROM chats WHERE owner_id = ?", (target,))
                user_chats = sql.fetchall()
                if not user_chats:
                        await message.reply("У пользователя нет зарегистрированных бесед.", disable_mentions=1)
                        return True

                
                page = 1
                per_page = 5
                total_pages = (len(user_chats) + per_page - 1) // per_page
                start = (page - 1) * per_page
                end = start + per_page
                page_chats = user_chats[start:end]

                all_chats = []
                for idx, (chat_id_val,) in enumerate(page_chats, start=1):
                        chat_title, link = await get_chat_display_info(chat_id_val)
                        all_chats.append(
                                f"{idx}. {chat_title} | 🆔: {chat_id_val} | 🔗 {format_chat_link_text(link)}"
                        )

                all_chats_text = "\n".join(all_chats)
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Назад", {"command": "infoidMinus", "page": 1, "user": target}), color=KeyboardButtonColor.NEGATIVE)
                    .add(Callback("Вперёд", {"command": "infoidPlus", "page": 1, "user": target}), color=KeyboardButtonColor.POSITIVE)
                )

                await message.reply(
                        f"❗ Список бесед @id{target} (пользователя):\n(Страница: 1)\n\n{all_chats_text}\n\n🗨️ Всего бесед у пользователя: {idx}",
                        disable_mentions=1,
                        keyboard=keyboard
                )
                return True                

        if command in ['banwords', 'запрещенныеслова', 'banwordlist']:
                if not await _has_role_level(user_id, chat_id, 11):
                        await message.replyLocalizedMessage('not_preminisionss')
                        return True

                sql.execute("SELECT word, creator_id, time FROM ban_words ORDER BY time DESC")
                rows = sql.fetchall()
                if not rows:
                        await message.reply("Запрещённые слова отсутствуют!", disable_mentions=1)
                        return True

                total = len(rows)
                per_page = 5
                max_page = (total + per_page - 1) // per_page

                async def get_words_page(page: int):
                        start = (page - 1) * per_page
                        end = start + per_page
                        formatted = []
                        for i, (word, creator, tm) in enumerate(rows[start:end], start=start + 1):
                                try:
                                        info = await bot.api.users.get(user_ids=creator)
                                        creator_name = f"{info[0].first_name} {info[0].last_name}"
                                except:
                                        creator_name = "Не удалось получить имя"
                                formatted.append(f"{i}. {word} | @id{creator} ({creator_name}) | Время: {tm}")
                        return formatted

                page = 1 
                page_data = await get_words_page(page)
                page_text = "\n\n".join(page_data)

                keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("⏪", {"command": "banwordsMinus", "page": 1}), color=KeyboardButtonColor.NEGATIVE)
                        .add(Callback("⏩", {"command": "banwordsPlus", "page": 1}), color=KeyboardButtonColor.POSITIVE)
                )

                await message.reply(
                        f"Запрещённые слова (Страница 1):\n\n{page_text}\n\nВсего запрещенных слов: {total}",
                        disable_mentions=1, keyboard=keyboard
                )
                return True
                
        if command in ['addbanwords', 'addword', 'banword']:
                if not await _has_role_level(user_id, chat_id, 11):
                        await message.replyLocalizedMessage('not_preminisionss')
                        return True
                if len(arguments) < 2:
                        await message.reply("Пример: /addbanwords текст")
                        return True

                word = arguments[1].lower()
                time_now = datetime.now().strftime("%I:%M %p")

                sql.execute("SELECT word FROM ban_words WHERE word = ?", (word,))
                if sql.fetchone():
                        await message.reply("Слово уже находиться в списке запрещенных слов!")
                        return True

                sql.execute("INSERT INTO ban_words (word, creator_id, time) VALUES (?, ?, ?)", (word, user_id, time_now))
                database.commit()

                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) добавил(-а) слово «{word}» в список запрещенных слов!")
                return True

        if command in ['removebanwords', 'unword', 'unbanword']:
                if not await _has_role_level(user_id, chat_id, 11):
                        await message.replyLocalizedMessage('not_preminisionss')
                        return True
                if len(arguments) < 2:
                        await message.reply("Пример: /removebanwords текст")
                        return True

                word = arguments[1].lower()
                sql.execute("SELECT word FROM ban_words WHERE word = ?", (word,))
                if not sql.fetchone():
                        await message.reply("Слово отсутствует в списке запрещенных слов!")
                        return True

                sql.execute("DELETE FROM ban_words WHERE word = ?", (word,))
                database.commit()

                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) удалил(-а) слово «{word}» из списка запрещенных слов!")
                return True
                
        
        if command in ['info', 'инфо', 'информация']:
            sql.execute("SELECT description FROM info WHERE chat_id = ?", (chat_id,))
            info_text = sql.fetchone()

            if not info_text:
                await message.replyLocalizedMessage('command_info_not')
                return True

            await message.replyLocalizedMessage('command_info', {
                        'info': info_text[0]
                    })
            return True

        if command in ['setinfo', 'установитьинфо']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2:
                await message.replyLocalizedMessage('command_setinfo_params')
                return True

            text = get_raw_text_tail(raw_text, 1)
            sql.execute("INSERT OR REPLACE INTO info (chat_id, description) VALUES (?, ?)", (chat_id, text))
            database.commit()

            await message.replyLocalizedMessage('command_setinfo', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'text': text
                    })            
            return True

        if command in ['antisliv', 'антислив']:
            if not await _can_use_command(user_id, chat_id, 6):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            current_mode = await get_antisliv(chat_id)
            new_mode = 0 if current_mode == 1 else 1

            
            await antisliv_mode(chat_id, new_mode)

            
            user_name = await get_user_name(user_id, chat_id)

            
            if new_mode == 1:
                text = f"@id{user_id} ({user_name}) включил(-а) систему антислива!"
            else:
                text = f"@id{user_id} ({user_name}) выключил(-а) систему антислива!"

            await message.replyLocalizedMessage('command_antisliv', {
                        'info': text
                    })            
            return True            
            
        if command in ['clearwarn', 'очиститьварны']:
            if not await _can_use_command(user_id, chat_id, 6):  
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            count = await clear_all_warns(chat_id)

            if count == 0:
                await message.replyLocalizedMessage('command_clearwarns_no_users')
            else:
                await message.replyLocalizedMessage('command_clearwarns', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'count_clear': count
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"очистил(-а) варны у {count} пользователей")            

            return True
            
        if command in ['getwarn', 'gwarn', 'getwarns', 'гетварн', 'гварн']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            warns = await gwarn(user, chat_id)
            string_info = str
            if not warns: string_info = "Активных предупреждений нет!"
            else: string_info = f"@id{warns['moder']} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("История предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )

            await message.replyLocalizedMessage('command_getwarn', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'warns': warns,
                        'info': string_info
                    }, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"посмотрел(-а) активные предупреждения @id{user} (пользователя)")            

        if command in ['zunban', 'удалитьбаны', 'снятьвсебаны']:
                if not await _has_role_level(user_id, chat_id, 11):
                    await message.replyLocalizedMessage('not_preminisionss')
                    return True

                target = int
                if message.reply_message:
                    target = message.reply_message.from_id
                elif len(arguments) >= 2 and await getID(arguments[1]):
                    target = await getID(arguments[1])
                else:
                    await message.reply("Укажите пользователя!")
                    return True

                sql.execute("SELECT chat_id FROM chats")
                chats_list = sql.fetchall()
                total_removed = 0

                for c in chats_list:
                    chat_id_check = c[0]
                    try:
                        sql.execute(f"DELETE FROM bans_{chat_id_check} WHERE user_id = ?", (target,))
                        removed = sql.rowcount
                        if removed > 0:
                            total_removed += removed
                    except:
                        continue

                database.commit()

                if total_removed > 0:
                    await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) удалил(-а) «{total_removed}» блокировку(-и) у @id{target} ({await get_user_name(target, chat_id)})", disable_mentions=1)
                    await chats_log(user_id=user_id, target_id=target, role=None, log=f"снял(-а) все баны @id{target}")
                else:
                    await message.reply(f"У @id{target} (пользователя) нет блокировок в чатах!", disable_mentions=1)
                return True                
                
        if command in ['clearbans', 'очиститьбаны']:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2 or not arguments[1].isdigit():
                await message.reply("Укажите чат!", disable_mentions=1)
                return True

            target_chat = int(arguments[1])
            table_name = f"bans_{target_chat}"

            sql.execute(f"SELECT user_id FROM {table_name}")
            users = sql.fetchall()

            if not users:
                await message.reply(f"В беседе ID {target_chat} нет активных блокировок!", disable_mentions=1)
                return True

            sql.execute(f"DELETE FROM {table_name}")
            database.commit()

            text_users = ""
            for i, (uid,) in enumerate(users, 1):
                username = await get_user_name(uid, chat_id)
                text_users += f"{i}) @id{uid} ({await get_user_name(uid, target_chat)})\n"

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}), снял(-а) блокировки в беседе «{target_chat}»\n\n"
                f"Пользователи у которых были сняты блокировки:\n{text_users}\nВсего блокировок снято: {len(users)}",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"очистил(-а) блокировки в {target_chat} ({len(users)})")
            return True

        if command in ['amnesty', 'амнистия']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            tables = {
                "mutes": f"mutes_{chat_id}",
                "bans": f"bans_{chat_id}",
                "warns": f"warns_{chat_id}"
            }

            result_text = ""
            total_cleared = {}

            for key, table in tables.items():
                sql.execute(f"SELECT user_id FROM {table}")
                users = sql.fetchall()
                count = len(users)
                total_cleared[key] = count

                sql.execute(f"DELETE FROM {table}")
                database.commit()

                if count > 0:
                    lines = "".join([f"{i+1}. @id{uid[0]} ({await get_user_name(user_id, chat_id)})\n" for i, uid in enumerate(users)])
                    result_text += f"Снято {key}: {count}\n| Из них:\n{lines}\n"
                else:
                    result_text += f"Снято {key}: 0\n| Из них: —\n\n"

            await message.replyLocalizedMessage('command_amnesty', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'result': result_text
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"провёл(-а) амнистию в беседе {chat_id}")
            return True                            

        if command in ['warnhistory', 'historywarns', 'whistory', 'историяварнов', 'историяпредов']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            warnhistory_mass = await warnhistory(user, chat_id)
            if not warnhistory_mass: wh_string = "Предупреждений не было!"
            else: wh_string = '\n'.join(warnhistory_mass)

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Активные предупреждения", {"command": "activeWarns", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("Вся информация", {"command": "stats", "user": user, "chatId": chat_id}),color=KeyboardButtonColor.PRIMARY)
            )

            await message.reply(f"Информация о всех предупреждениях @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}", disable_mentions=1, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"посмотрел(-а) все предупреждения @id{user} (пользователя)")            


        if command in ['say', 'сообщение']:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2:
                await message.reply("Укажите айди беседы!")
                return True

            
            try:
                target_chat = int(arguments[1])
            except ValueError:
                await message.reply("Укажите конкретный айди беседы!")
                return True

            
            if target_chat > 0:
                target_peer = 2000000000 + target_chat
            else:
                target_peer = target_chat

            
            text = get_raw_text_tail(raw_text, 2)
            if not text or not text.strip():
                await message.reply("Укажите текст сообщения!")
                return True

            try:
                await bot.api.messages.send(
                    peer_id=target_peer,
                    message=text,
                    random_id=0
                )
                await message.reply(f"Сообщение успешно отправлено в чат ID {target_chat}.")
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"отправил(-а) сообщение в чат «{target_chat}» Сообщение: {text}")            
            except Exception as e:
                await message.reply(f"Произошла ошибка при отправке: {e}")
                print(f"[say command] Ошибка отправки в чат {target_chat}: {e}")
            return True
            
        

        if command in ['getban', 'чекбан', 'гетбан', 'checkban']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            target = None
            if message.reply_message:
                target = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            sql.execute("SELECT * FROM globalban WHERE user_id = ?", (target,))
            globalban_entry = sql.fetchone()

            sql.execute("SELECT * FROM gbanlist WHERE user_id = ?", (target,))
            gbanlist_entry = sql.fetchone()

            sql.execute("SELECT * FROM games_blacklist WHERE user_id = ?", (target,))
            games_blacklist_entry = sql.fetchone()

            globalbans_chats = ""
            if globalban_entry:
                globalbans_chats += f"Блокировка во всех беседах — @id{globalban_entry[1]} (Модератор) | {globalban_entry[2]} | {globalban_entry[3]} МСК (UTC+3)"
            else:
                globalbans_chats += "Блокировка во всех беседах — отсутствует"

            if gbanlist_entry:
                globalbans_chats += f"\nБлокировка в беседах игроков — @id{gbanlist_entry[1]} (Модератор) | {gbanlist_entry[2]} | {gbanlist_entry[3]} МСК (UTC+3)"
            else:
                globalbans_chats += "\nБлокировка в беседах игроков — отсутствует"

            if games_blacklist_entry:
                globalbans_chats += f"\nЧС игр (блокировка игровых команд) — @id{games_blacklist_entry[1]} (Модератор) | {games_blacklist_entry[2]} | {games_blacklist_entry[3]} МСК (UTC+3)"
            else:
                globalbans_chats += "\nЧС игр (блокировка игровых команд) — отсутствует"

            
            sql.execute("SELECT chat_id FROM chats")
            chats_list = sql.fetchall()
            bans = ""
            count_bans = 0
            i = 1
            for c in chats_list:
                chat_id_check = c[0]
                try:
                    sql.execute(f"SELECT moder, reason, date FROM bans_{chat_id_check} WHERE user_id = ?", (target,))
                    user_bans = sql.fetchall()
                    if user_bans:
                        
                        rel_id = 2000000000 + chat_id_check
                        try:
                            resp = await bot.api.messages.get_conversations_by_id(peer_ids=rel_id)
                            if resp.items:
                                chat_title = resp.items[0].chat_settings.title or "Без названия"
                            else:
                                chat_title = "Без названия"
                        except:
                            chat_title = "Ошибка получения названия"

                        count_bans += 1
                        for ub in user_bans:
                            mod, reason, date = ub
                            bans += f"{i}) {chat_title} | @id{mod} (Модератор) | {reason} | {date} МСК (UTC+3)\n"
                            i += 1
                except:
                    continue  
                                       
            if count_bans == 0:
                bans_chats = "Блокировки в беседах отсутствуют"
            else:
                bans_chats = f"Количество бесед, в которых заблокирован пользователь: {count_bans}\nИнформация о банах пользователя:\n{bans}"

            
            await message.replyLocalizedMessage('command_getban', {
                        'target': f'@id{target} (Пользователь)',
                        'gbans': globalbans_chats,
                        'banschats': bans_chats
                    })

            await chats_log(
                user_id=user_id,
                target_id=target,
                role=None,
                log=f"посмотрел(-а) список блокировок @id{target} (пользователя)"
            )
            return True
                        
        

        if command in ["чекбд"]:
            if user_id not in (488828183, 574393629):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            try:
                integrity = sql.execute("PRAGMA integrity_check").fetchone()[0]
                page_count = sql.execute("PRAGMA page_count").fetchone()[0]
                page_size = sql.execute("PRAGMA page_size").fetchone()[0]
                db_size_mb = round(page_count * page_size / 1024 / 1024, 1)
                chat_rows = sql.execute("SELECT chat_id, type FROM chats").fetchall()
                total_chats = len(chat_rows)
                active_chat_rows, inactive_chat_rows = await get_active_registered_chats()
                broken_tables = []
                checked = 0
                for cid, ctype in chat_rows:
                    for suffix in ("permissions", "mutes", "warns", "bans", "nicks", "banwords", "messages", "custom_roles"):
                        tbl = f"{suffix}_{cid}"
                        try:
                            sql.execute(f"SELECT COUNT(*) FROM [{tbl}]")
                            checked += 1
                        except Exception as e:
                            err_text = str(e)[:60]
                            broken_tables.append(f"{tbl}: {err_text}")
                lines = [
                    f"🔍 Проверка базы данных",
                    f"",
                    f"📋 Integrity: {integrity}",
                    f"💾 Размер: {db_size_mb} MB",
                    f"💬 Чатов в базе: {total_chats}",
                    f"✅ Активных чатов с админкой бота: {len(active_chat_rows)}",
                    f"🚫 Скрыто из /чаты: {len(inactive_chat_rows)}",
                    f"✅ Таблиц проверено: {checked}",
                ]
                if inactive_chat_rows:
                    lines.append("")
                    lines.append("Недоступные беседы:")
                    for cid, _owner_id, reason in inactive_chat_rows[:10]:
                        lines.append(f"  • {cid}: {reason}")
                    if len(inactive_chat_rows) > 10:
                        lines.append(f"  ...и ещё {len(inactive_chat_rows) - 10}")
                if broken_tables:
                    lines.append(f"⚠️ Сломанных таблиц: {len(broken_tables)}")
                    for bt in broken_tables[:10]:
                        lines.append(f"  • {bt}")
                    if len(broken_tables) > 10:
                        lines.append(f"  ...и ещё {len(broken_tables) - 10}")
                else:
                    lines.append(f"✅ Все таблицы в порядке")
                await message.reply("\n".join(lines), disable_mentions=1)
            except Exception as e:
                await message.reply(f"❌ Ошибка проверки БД: {str(e)[:200]}", disable_mentions=1)
            return True

        if command in ["фиксбд"]:
            if user_id not in (488828183, 574393629):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            try:
                chat_rows = sql.execute("SELECT chat_id FROM chats").fetchall()
                table_suffixes = [
                    ("custom_roles", "level INTEGER PRIMARY KEY, name TEXT NOT NULL"),
                    ("permissions", "user_id BIGINT, level BIGINT"),
                    ("nicks", "user_id BIGINT, nick TEXT"),
                    ("warns", "user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT"),
                    ("mutes", "user_id BIGINT, moder TEXT, reason TEXT, date BIGINT, date_string TEXT, time BIGINT"),
                    ("mutelogs", "user_id BIGINT, moder_id BIGINT, reason TEXT, date BIGINT, date_string TEXT, mute_time BIGINT, status TEXT"),
                    ("bans", "user_id BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT"),
                    ("banwords", "banword TEXT"),
                    ("messages", "user_id BIGINT, date BIGINT, date_string TEXT, message_id BIGINT, cmid BIGINT"),
                    ("warnhistory", "user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT"),
                    ("punishments", "user_id BIGINT, date TEXT"),
                ]
                created = 0
                skipped = 0
                errors = 0
                for (cid,) in chat_rows:
                    for suffix, cols in table_suffixes:
                        tbl = f"{suffix}_{cid}"
                        try:
                            sql.execute(f"CREATE TABLE IF NOT EXISTS [{tbl}] ({cols})")
                            created += 1
                        except Exception as e:
                            errors += 1
                
                for (cid,) in chat_rows:
                    try:
                        cnt = sql.execute(f"SELECT COUNT(*) FROM [custom_roles_{cid}]").fetchone()[0]
                        if cnt == 0:
                            sql.execute(f"INSERT OR IGNORE INTO [custom_roles_{cid}] (level, name) VALUES (100, 'Владелец беседы')")
                    except:
                        pass
                database.commit()
                await message.reply(
                    f"🔧 Исправление базы данных\n\n"
                    f"💬 Чатов: {len(chat_rows)}\n"
                    f"✅ Таблиц создано/проверено: {created}\n"
                    f"⏭ Уже существовало: пропущено\n"
                    f"❌ Ошибок: {errors}\n\n"
                    f"База данных восстановлена.",
                    disable_mentions=1,
                )
            except Exception as e:
                await message.reply(f"❌ Ошибка восстановления БД: {str(e)[:200]}", disable_mentions=1)
            return True

        if command in ["restart", "рестарт"]:
            global_level = await get_global_manager_level(user_id)
            mapped = GLOBAL_ROLE_LEVELS.get(global_level, 0)
            if mapped < 16:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            progress_message_id = None
            restart_started_at = time.time()
            restart_estimate_seconds = _load_restart_progress_estimate()
            start_progress_text = _build_restart_progress_text(0)
            try:
                send_result = await message.answer(
                    start_progress_text,
                    random_id=random.randint(1, 2_147_483_647),
                    disable_mentions=1,
                )
                progress_message_id = await _resolve_sent_message_cmid(message.peer_id, send_result)
                print(f"[restart] progress cmid={progress_message_id} send_result={send_result}")
            except Exception as error:
                print(f"[restart] send progress message error: {error}")
                await message.reply(start_progress_text, disable_mentions=1)

            progress_editing = progress_message_id is not None
            if progress_editing:
                _save_pending_restart_progress(
                    message.peer_id,
                    progress_message_id,
                    restart_started_at,
                    restart_estimate_seconds,
                )
                _start_restart_progress_worker(
                    message.peer_id,
                    progress_message_id,
                    restart_started_at,
                    restart_estimate_seconds,
                )
                await asyncio.sleep(0.8)
                elapsed_percent = _restart_elapsed_percent(restart_started_at, restart_estimate_seconds)
                if elapsed_percent > 0:
                    await _edit_bot_message(
                        message.peer_id,
                        progress_message_id,
                        _build_restart_progress_text(elapsed_percent),
                        flood_retries=0,
                    )

            await asyncio.sleep(0.2)
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as error:
                _clear_pending_restart_progress()
                error_text = f"Не удалось перезапустить бот: {error}"
                if not progress_editing or not await _edit_bot_message(message.peer_id, progress_message_id, error_text, flood_retries=2):
                    await message.reply(error_text, disable_mentions=1)
                return True

        if command in ["рефреш", "refresh"]:
            if user_id not in (488828183, 574393629):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            await message.reply("🔄 Отправляю пинг во все чаты...", disable_mentions=1)
            chat_rows = sql.execute("SELECT chat_id, peer_id FROM chats").fetchall()
            ok_count = 0
            fail_count = 0
            fail_list = []
            for cid, pid in chat_rows:
                try:
                    await bot.api.messages.send(
                        peer_id=int(pid),
                        random_id=0,
                        message="🔧 Проверка активности бота",
                        disable_mentions=1,
                    )
                    ok_count += 1
                except Exception as e:
                    fail_count += 1
                    fail_list.append(f"{cid}: {str(e)[:50]}")
                await asyncio.sleep(0.35)
            result_lines = [
                f"📡 Пинг завершён",
                f"",
                f"✅ Успешно: {ok_count}",
                f"❌ Ошибок: {fail_count}",
            ]
            if fail_list:
                result_lines.append("")
                result_lines.append("Проблемные:")
                for fl in fail_list[:10]:
                    result_lines.append(f"  • {fl}")
            await message.reply("\n".join(result_lines), disable_mentions=1)
            return True


            



            





        if command in ["settypetech"]:
            await message.reply(
                "🛠 Команда /settypetech больше не используется.\n"
                "Теперь для технических доступов используйте глобальную роль «Технический специалист».",
                disable_mentions=1,
            )
            return True

        tech_role_commands = {
            "addjtech": {"level": 1, "required": 2, "aliases": ["addjtech", "млтех"]},
            "addtech": {"level": 2, "required": 3, "aliases": ["addtech"]},
            "addstech": {"level": 3, "required": 4, "aliases": ["addstech", "стех"]},
            "addctech": {"level": 4, "required": 5, "aliases": ["addctech"]},
            "addztech": {"level": 5, "required": 6, "aliases": ["addztech"]},
        }
        matched_tech_command = next((cfg for cfg in tech_role_commands.values() if command in cfg["aliases"]), None)
        if matched_tech_command:
            await message.reply(
                "🛠 Старая система технических ролей отключена.\n"
                "Используйте глобальную роль «Технический специалист».",
                disable_mentions=1,
            )
            return True

        if command in ["removetech", "rtech", "снятьтех"]:
            await message.reply(
                "🛠 Старая система технических ролей отключена.\n"
                "Используйте глобальные роли.",
                disable_mentions=1,
            )
            return True

            
        if command in ['settingsmute', 'настройкимута']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            sql.execute("SELECT * FROM mutesettings WHERE chat_id = ?", (chat_id,))
            row = sql.fetchone()
            if row is None:
                sql.execute("INSERT INTO mutesettings (chat_id, mode) VALUES (?, ?)", (chat_id, 1))
                database.commit()
                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), включил(-а) систему выдачи варнов в муте!", disable_mentions=1)
            else:
                new_mode = 0 if row[1] == 1 else 1
                sql.execute("UPDATE mutesettings SET mode = ? WHERE chat_id = ?", (new_mode, chat_id))
                database.commit()
                if new_mode == 0:
                    await message.replyLocalizedMessage('command_settingsmute_off')
                else:
                    await message.replyLocalizedMessage('command_settingsmute_on')

            return True            


        

        if command in ['warnlist', 'warns', 'wlist', 'варны', 'варнлист']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            warns = await warnlist(chat_id)
            if warns == False: warns_string = "Пользователей с предупреждениями нет!"
            else: warns_string = '\n'.join(warns)

            await message.replyLocalizedMessage('command_warnlist', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'warns': warns,
                        'info': warns_string
                    })

        if command in ['staff', 'стафф']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            
            
            if await _is_modified_chat(chat_id):
                sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
                sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT)")
                database.commit()

                sql.execute(
                    f"SELECT level, name FROM custom_roles_{chat_id} "
                    "WHERE level BETWEEN 1 AND 100 ORDER BY level DESC"
                )
                all_roles = sql.fetchall() or []

                sql.execute(f"SELECT user_id, level FROM permissions_{chat_id}")
                all_permissions = sql.fetchall() or []

                members_by_level = {int(level): [] for level, _ in all_roles}
                for assigned_user_id, assigned_level in all_permissions:
                    try:
                        assigned_level = int(assigned_level)
                    except (TypeError, ValueError):
                        continue
                    if assigned_level not in members_by_level or assigned_level == 100:
                        continue
                    members_by_level[assigned_level].append(
                        format_vk_link(
                            assigned_user_id,
                            await get_user_name(assigned_user_id, chat_id)
                        )
                    )

                owner_row = await db_execute(
                    "SELECT owner_id FROM chats WHERE chat_id = ?",
                    (chat_id,),
                    fetch='one'
                )
                owner_ids = []
                if owner_row and owner_row[0]:
                    owner_ids.append(int(owner_row[0]))

                coowner_rows = await db_execute(
                    "SELECT user_id FROM chat_coowners WHERE chat_id = ? ORDER BY created_at, user_id",
                    (chat_id,),
                    fetch='all'
                )
                for coowner_row in (coowner_rows or []):
                    coowner_id = int(coowner_row[0])
                    if coowner_id not in owner_ids:
                        owner_ids.append(coowner_id)

                lines = []
                for role_level, role_name in all_roles:
                    role_level = int(role_level)
                    if role_level == 100:
                        owner_members = [
                            format_vk_link(owner_id, await get_user_name(owner_id, chat_id))
                            for owner_id in owner_ids
                        ]
                        owner_text = "\n".join(owner_members) if owner_members else "Отсутствуют"
                        lines.append(f"{role_name} -- {owner_text}")
                        continue

                    lines.append(f"\n{role_name}:")
                    role_members = members_by_level.get(role_level, [])
                    lines.append('\n'.join(role_members) if role_members else "Отсутствуют")

                await message.reply('\n'.join(lines), disable_mentions=1)
                await chats_log(
                    user_id=user_id, target_id=None, role=None,
                    log="посмотрел(-а) список администрации в чате"
                )
                return True

            staff_mass = await staff(chat_id)

            if staff_mass is None:
                staff_str = "В данной беседе нет пользователей с ролями!"
                await message.reply(staff_str, disable_mentions=1)
                return True
            elif 'custom_sorted' in staff_mass:
                owner_role_name = await _get_custom_role_name(chat_id, 100)
                lines = [f"{owner_role_name} -- Отсутствуют"]
                for role_name, members in staff_mass['custom_sorted']:
                    if role_name == owner_role_name:
                        members_text = "\n".join(members) if members else "Отсутствуют"
                        lines[0] = f"{owner_role_name} -- {members_text}"
                        continue
                    lines.append(f"\n{role_name}:")
                    lines.append('\n'.join(members) if members else "Отсутствуют")
                await message.reply('\n'.join(lines), disable_mentions=1)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список администрации в чате")
                return True
            else:
                moders = '\n'.join(staff_mass['moders']) if staff_mass['moders'] else "Отсутствуют"
                stmoders = '\n'.join(staff_mass['stmoders']) if staff_mass['stmoders'] else "Отсутствуют"
                admins = '\n'.join(staff_mass['admins']) if staff_mass['admins'] else "Отсутствуют"
                stadmins = '\n'.join(staff_mass['stadmins']) if staff_mass['stadmins'] else "Отсутствуют"
                zsa = '\n'.join(staff_mass['zamspecadm']) if staff_mass['zamspecadm'] else "Отсутствуют"
                sa = '\n'.join(staff_mass['specadm']) if staff_mass['specadm'] else "Отсутствуют"

                owner = await get_chat_owner_display(chat_id)

                await message.reply(
                    f"Владелец беседы -- {owner}\n\n"
                    f"Спец. администраторы:\n"
                    f"{sa}\n\n"
                    f"Зам. спец администратора:\n"
                    f"{zsa}\n\n"
                    f"Старшие администраторы:\n"
                    f"{stadmins}\n\n"
                    f"Администраторы:\n"
                    f"{admins}\n\n"
                    f"Старшие модераторы:\n"
                    f"{stmoders}\n\n"
                    f"Модераторы:\n"
                    f"{moders}",
                    disable_mentions=1,
                )
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список администрации в чате")            
                return True              
                
        if command in ['gstaff', 'гстафф']:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            staff_mass = await staff(chat_id)

            if staff_mass is None:
                staff_str = "В данной беседе нет пользователей с глобальными ролями!"
                await message.reply(staff_str, disable_mentions=1)
                return True
            else:
                moders = '\n'.join(staff_mass['moders']) if staff_mass['moders'] else "Отсутствуют"
                stmoders = '\n'.join(staff_mass['stmoders']) if staff_mass['stmoders'] else "Отсутствуют"
                admins = '\n'.join(staff_mass['admins']) if staff_mass['admins'] else "Отсутствуют"
                stadmins = '\n'.join(staff_mass['stadmins']) if staff_mass['stadmins'] else "Отсутствуют"
                zsa = '\n'.join(staff_mass['zamspecadm']) if staff_mass['zamspecadm'] else "Отсутствуют"
                sa = '\n'.join(staff_mass['specadm']) if staff_mass['specadm'] else "Отсутствуют"
                bloggers = '\n'.join(staff_mass['bloggers']) if staff_mass['bloggers'] else "Отсутствуют"
                tech_specs = '\n'.join(staff_mass['tech_specs']) if staff_mass['tech_specs'] else "Отсутствуют"
                tech_curators = '\n'.join(staff_mass['tech_curators']) if staff_mass['tech_curators'] else "Отсутствуют"
                zamruk = '\n'.join(staff_mass['zamruk']) if staff_mass['zamruk'] else "Отсутствуют"
                oszamruk = '\n'.join(staff_mass['oszamruk']) if staff_mass['oszamruk'] else "Отсутствуют"
                ruk = '\n'.join(staff_mass['ruk']) if staff_mass['ruk'] else "Отсутствуют"
                dev = '\n'.join(staff_mass['dev']) if staff_mass['dev'] else "Отсутствуют"
                quality_control = '\n'.join(staff_mass['quality_control']) if staff_mass['quality_control'] else "Отсутствуют"
                zamglt = '\n'.join(staff_mass['zamglt']) if staff_mass['zamglt'] else "Отсутствуют"
                glt = '\n'.join(staff_mass['glt']) if staff_mass['glt'] else "Отсутствуют"

                x = await bot.api.messages.get_conversations_by_id(
                    peer_ids=peer_id,
                    extended=1,
                    fields='chat_settings',
                    group_id=message.group_id
                )
                x = json.loads(x.model_dump_json())
                for i in x['items']:
                    owner = int(i["chat_settings"]["owner_id"])

                if owner < 1:
                    owner = f"[club{abs(owner)}|ADMIN MANAGER]"
                else:
                    owner = format_vk_link(owner, "ADMIN MANAGER")

                await message.reply(
                    f"💻 | Разработчики бота:\n{dev}\n\n"
                    f"🙎‍♂️ | {GLOBAL_QUALITY_CONTROL_NAME}:\n{quality_control}\n\n"
                    f"⭐️ | Директор бота:\n{ruk}\n\n"
                    f"🔧 | Главный Тех. Специалист:\n{glt}\n\n"
                    f"🔧 | Зам. Главного Тех. Специалиста:\n{zamglt}\n\n"
                    f"🔧 | {GLOBAL_TECH_CURATOR_NAME}:\n{tech_curators}\n\n"
                    f"💫 | Осн. заместители директора:\n{oszamruk}\n\n"
                    f"✨ | Заместители директора:\n{zamruk}\n\n"
                    f"🛠 | Технические специалисты:\n{tech_specs}\n\n"
                    f"📸 | Медиа Партнеры:\n{bloggers}",
                    disable_mentions=1
                )
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) глобальный список администрации в чате")            
                return True                                                

        modified_only_commands = {
            'роли', 'roles',
            'unmrole', 'снятьмроль',
            'createrole', 'создатьроль',
            'delrole', 'удалитьроль',
            'rename', 'rerole',
            'role', 'роль',
            'smsrole', 'смсrole',
            'unrole', 'унроль',
            'unsmsrole', 'унсмсроль'
        }
        if command in modified_only_commands and not await _is_modified_chat(chat_id):
            await message.reply("Сначала включите /mrole!", disable_mentions=1)
            return True

        local_role_only_commands = {
            'addmoder', 'moder', 'модер',
            'removerole', 'rrole', 'снятьроль',
            'addsenmoder', 'senmoder', 'смодер',
            'addadmin', 'admin', 'админ',
            'addsenadmin', 'addsenadm', 'senadm', 'senadmin', 'садмин',
            'addzsa', 'зса',
            'addsa', 'са', 'spec', 'specadm',
            'srole', 'сроле',
            'sremoverole', 'srrole'
        }
        if command in local_role_only_commands and await _is_modified_chat(chat_id):
            await message.reply(
                "В модифицированном чате используйте /role, /unrole, /smsrole или /unsmsrole.",
                disable_mentions=1
            )
            return True

        
        if command in ['роли', 'roles']:
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True

            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
            database.commit()

            roles_fetch = await db_execute(
                f"SELECT level, name FROM custom_roles_{chat_id} WHERE level BETWEEN 1 AND 100 ORDER BY level DESC",
                fetch='all'
            )
            if not roles_fetch:
                await message.reply("В этой беседе роли не созданы.", disable_mentions=1)
                return True

            lines = ["Роли беседы:"]
            for level, role_name in roles_fetch:
                lines.append(f"{role_name} - {level}")

            await message.reply('\n'.join(lines), disable_mentions=1)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список ролей модифицированного чата")
            return True

        
        if command in ['mrole', 'мроль']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            was_modified = await _is_modified_chat(chat_id)
            sql.execute("INSERT OR IGNORE INTO modified_chats (chat_id) VALUES (?)", (chat_id,))
            sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
            sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
            if not was_modified:
                sql.execute(f"DELETE FROM permissions_{chat_id}")
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} установил модифицированные роли в беседе",
                disable_mentions=1
            )
            return True

        
        if command in ['unmrole', 'снятьмроль']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if not await _is_modified_chat(chat_id):
                await message.reply("В данной беседе модифицированные роли не включены!", disable_mentions=1)
                return True

            sql.execute("DELETE FROM modified_chats WHERE chat_id = ?", (chat_id,))
            sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
            sql.execute(f"DELETE FROM permissions_{chat_id}")
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} отключил модифицированные роли в беседе",
                disable_mentions=1
            )
            return True

        
        if command in ['createrole', 'создатьроль']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True
            if len(arguments) < 3:
                await message.reply("Использование: /createrole <уровень 0-99> <название>", disable_mentions=1)
                return True

            if not arguments[1].isdigit():
                await message.reply("Уровень должен быть числом!", disable_mentions=1)
                return True

            level = int(arguments[1])
            role_name = ' '.join(arguments[2:])
            if level < 1 or level > 99:
                await message.reply("Уровень должен быть от 1 до 99!", disable_mentions=1)
                return True

            sql.execute(f"INSERT OR REPLACE INTO custom_roles_{chat_id} (level, name) VALUES (?, ?)", (level, role_name))
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} установил новую роль — {role_name} с уровнем прав {level}",
                disable_mentions=1
            )
            return True

        
        if command in ['delrole', 'удалитьроль']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True
            if len(arguments) < 2 or not arguments[1].isdigit():
                await message.reply("Использование: /delrole <уровень>", disable_mentions=1)
                return True

            level = int(arguments[1])
            if level == 100:
                await message.reply("Нельзя удалить роль владельца беседы!", disable_mentions=1)
                return True

            role_name = await _get_custom_role_name(chat_id, level)

            sql.execute(f"DELETE FROM custom_roles_{chat_id} WHERE level = ?", (level,))
            sql.execute(f"DELETE FROM permissions_{chat_id} WHERE level = ?", (level,))
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} удалил роль «{role_name}» (уровень {level})",
                disable_mentions=1
            )
            return True

        
        if command in ['rename']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True
            sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (0, 'Пользователь')")
            sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
            database.commit()
            if len(arguments) < 3 or not arguments[1].isdigit():
                await message.reply("Использование: /rename <уровень> <новое название>", disable_mentions=1)
                return True

            level = int(arguments[1])
            if level < 0 or level > 100:
                await message.reply("Уровень должен быть от 0 до 100!", disable_mentions=1)
                return True

            new_role_name = (await get_string(arguments, 2) or "").strip()
            if not new_role_name:
                await message.reply("Укажите новое название роли!", disable_mentions=1)
                return True

            role_fetch = await db_execute(
                f"SELECT name FROM custom_roles_{chat_id} WHERE level = ?",
                (level,), fetch='one'
            )
            if not role_fetch:
                await message.reply("Данного уровня прав не существует!", disable_mentions=1)
                return True

            old_role_name = role_fetch[0]
            sql.execute(f"UPDATE custom_roles_{chat_id} SET name = ? WHERE level = ?", (new_role_name, level))
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} переименовал роль «{old_role_name}» в «{new_role_name}» (уровень {level})",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"переименовал роль «{old_role_name}» в «{new_role_name}»")
            return True

        
        if command in ['rerole']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True
            sql.execute(f"CREATE TABLE IF NOT EXISTS custom_roles_{chat_id} (level INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            sql.execute(f"INSERT OR IGNORE INTO custom_roles_{chat_id} (level, name) VALUES (100, 'Владелец беседы')")
            sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
            if len(arguments) < 3 or not arguments[1].isdigit() or not arguments[2].isdigit():
                await message.reply("Использование: /rerole <старый уровень> <новый уровень>", disable_mentions=1)
                return True

            old_level = int(arguments[1])
            new_level = int(arguments[2])
            if old_level < 1 or old_level > 99 or new_level < 1 or new_level > 99:
                await message.reply("Уровень должен быть от 1 до 99!", disable_mentions=1)
                return True
            if old_level == new_level:
                await message.reply("Старый и новый уровень совпадают!", disable_mentions=1)
                return True

            role_fetch = await db_execute(
                f"SELECT name FROM custom_roles_{chat_id} WHERE level = ?",
                (old_level,), fetch='one'
            )
            if not role_fetch:
                await message.reply("Роль со старым уровнем не найдена!", disable_mentions=1)
                return True

            busy_fetch = await db_execute(
                f"SELECT 1 FROM custom_roles_{chat_id} WHERE level = ?",
                (new_level,), fetch='one'
            )
            if busy_fetch:
                await message.reply("Новый уровень уже занят другой ролью!", disable_mentions=1)
                return True

            role_name = role_fetch[0]
            sql.execute(f"UPDATE custom_roles_{chat_id} SET level = ? WHERE level = ?", (new_level, old_level))
            sql.execute(f"UPDATE permissions_{chat_id} SET level = ? WHERE level = ?", (new_level, old_level))
            database.commit()

            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} изменил уровень роли «{role_name}»: {old_level} -> {new_level}",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"изменил уровень роли «{role_name}»: {old_level} -> {new_level}")
            return True

        
        if command in ['role', 'роль']:
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True

            my_role = await get_role(user_id, chat_id)

            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target_user = None
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])

            if not target_user:
                await message.replyLocalizedMessage('select_user')
                return True

            level_str = arguments[-1] if arguments else ''
            if not level_str.isdigit():
                await message.reply("Укажите уровень прав числом!", disable_mentions=1)
                return True

            level = int(level_str)

            primary_owner_id = await _get_primary_chat_owner_id(chat_id)
            target_is_primary_owner = int(target_user) == int(primary_owner_id)
            target_is_coowner = await _is_additional_chat_owner(target_user, chat_id)

            
            
            if level == 100:
                if not await _is_primary_chat_owner(user_id, chat_id):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
                if target_is_primary_owner:
                    await message.reply("Этот пользователь уже является основным владельцем беседы!", disable_mentions=1)
                    return True
                if target_is_coowner:
                    await message.reply("Этот пользователь уже является владельцем беседы!", disable_mentions=1)
                    return True

                sql.execute(
                    "INSERT OR IGNORE INTO chat_coowners (chat_id, user_id, granted_by, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, target_user, user_id, msk_now_str())
                )
                sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (target_user,))
                database.commit()

                role_name = await _get_custom_role_name(chat_id, 100)
                user_link = await format_vk_entity_link(user_id, chat_id)
                target_link = await format_vk_entity_link(target_user, chat_id)
                await message.reply(
                    f"{user_link} выдал права {target_link} — {role_name}",
                    disable_mentions=1
                )
                await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"выдал(-а) права «{role_name}» пользователю")
                return True

            
            
            if target_is_primary_owner or target_is_coowner:
                await message.reply("Нельзя изменить роль владельца беседы!", disable_mentions=1)
                return True

            if await _is_modified_chat(chat_id):
                if level <= 0 or level > 99:
                    await message.reply("Уровень от 1 до 99!", disable_mentions=1)
                    return True
                my_real_level = _modified_real_role_level(my_role)
                target_role = await get_role(target_user, chat_id)
                target_real_level = _modified_real_role_level(target_role) if _is_encoded_modified_role(target_role) else target_role
                if not _is_encoded_global_role(my_role) and (level >= my_real_level or target_real_level >= my_real_level):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
                try:
                    check = await db_execute(
                        f"SELECT 1 FROM custom_roles_{chat_id} WHERE level = ?",
                        (level,), fetch='one'
                    )
                except:
                    check = None
                if not check:
                    await message.reply("Данного уровня прав не существует!", disable_mentions=1)
                    return True
            else:
                if level < 1 or level > 6:
                    await message.reply("Доступны уровни от 1 до 6!", disable_mentions=1)
                    return True
                target_role = await get_role(target_user, chat_id)
                if not _is_encoded_global_role(my_role) and (level >= _effective_standard_role_level(my_role) or _effective_standard_role_level(target_role) >= _effective_standard_role_level(my_role)):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

            await roleG(target_user, chat_id, level)

            if await _is_modified_chat(chat_id):
                role_name = await _get_custom_role_name(chat_id, level)
            else:
                role_names = {1: "модератора", 2: "старшего модератора", 3: "администратора", 4: "старшего администратора", 5: "зам. спец администратора", 6: "спец. администратора"}
                role_name = role_names.get(level, f"уровень {level}")

            user_link = await format_vk_entity_link(user_id, chat_id)
            target_link = await format_vk_entity_link(target_user, chat_id)
            await message.reply(
                f"{user_link} выдал права {target_link} — {role_name}",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"выдал(-а) права «{role_name}» пользователю")
            return True

        
        if command in ['smrole', 'смrole']:
            if not await _is_chat_owner_or_global(user_id, chat_id):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Пожалуйста подключите через /server", disable_mentions=1)
                return True

            server_chats = list(dict.fromkeys(int(cid) for cid in server_chats if cid))
            if chat_id not in server_chats:
                server_chats.insert(0, chat_id)

            if await _is_modified_chat(chat_id):
                source_roles, source_permissions = _get_modified_role_snapshot(chat_id)
            else:
                source_roles, source_permissions = _get_local_role_snapshot_as_modified(chat_id)
            updated_chats = 0

            for cid in server_chats:
                try:
                    _apply_modified_role_snapshot(cid, source_roles, source_permissions)
                    updated_chats += 1
                except Exception as e:
                    print(f"[SMROLE] Ошибка синхронизации ролей в беседе {cid}: {e}")
            database.commit()

            if updated_chats <= 0:
                await message.reply("Не удалось синхронизировать роли в сетке бесед.", disable_mentions=1)
                return True

            server_number = await get_current_server(chat_id) or "?"
            user_link = await format_vk_entity_link(user_id, chat_id)
            await message.reply(
                f"{user_link} включил модифицированные роли в сетке бесед — сервер №{server_number}",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=None, role=None, log="включил(-а) модифицированные роли в сетке бесед с автовыдачей ролей")
            return True

        
        if command in ['smsrole', 'смсrole']:
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True

            my_role = await get_role(user_id, chat_id)

            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target_user = None
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])

            if not target_user:
                await message.replyLocalizedMessage('select_user')
                return True

            if not arguments[-1].isdigit():
                await message.reply("Укажите уровень прав числом!", disable_mentions=1)
                return True

            level = int(arguments[-1])

            if await _is_modified_chat(chat_id):
                if level <= 0 or level > 99:
                    await message.reply("Уровень от 1 до 99!", disable_mentions=1)
                    return True
                my_real_level = _modified_real_role_level(my_role)
                target_role = await get_role(target_user, chat_id)
                target_real_level = _modified_real_role_level(target_role) if _is_encoded_modified_role(target_role) else target_role
                if not _is_encoded_global_role(my_role) and (level >= my_real_level or target_real_level >= my_real_level):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
                try:
                    check = await db_execute(
                        f"SELECT 1 FROM custom_roles_{chat_id} WHERE level = ?",
                        (level,), fetch='one'
                    )
                except:
                    check = None
                if not check:
                    await message.reply("Данного уровня прав не существует!", disable_mentions=1)
                    return True
            else:
                if level < 1 or level > 6:
                    await message.reply("Доступны уровни от 1 до 6!", disable_mentions=1)
                    return True
                target_role = await get_role(target_user, chat_id)
                if not _is_encoded_global_role(my_role) and (level >= _effective_standard_role_level(my_role) or _effective_standard_role_level(target_role) >= _effective_standard_role_level(my_role)):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Пожалуйста подключите через /server", disable_mentions=1)
                return True

            applied_chats = 0
            for cid in server_chats:
                try:
                    target_level = await _smsrole_level_for_target_chat(chat_id, cid, level)
                    if target_level is None:
                        continue
                    await roleG(target_user, cid, target_level)
                    applied_chats += 1
                except Exception as e:
                    print(f"[SMSROLE] Ошибка при выдаче роли в беседе {cid}: {e}")

            if applied_chats <= 0:
                await message.reply("Не найдено бесед сетки с подходящим форматом этой роли.", disable_mentions=1)
                return True

            if await _is_modified_chat(chat_id):
                role_name = await _get_custom_role_name(chat_id, level)
            else:
                role_names = {1: "модератора", 2: "старшего модератора", 3: "администратора", 4: "старшего администратора", 5: "зам. спец администратора", 6: "спец. администратора"}
                role_name = role_names.get(level, f"уровень {level}")

            user_link = await format_vk_entity_link(user_id, chat_id)
            target_link = await format_vk_entity_link(target_user, chat_id)
            await message.reply(
                f"{user_link} выдал права {target_link} — {role_name} в сетке бесед",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"выдал(-а) права «{role_name}» в сетке бесед пользователю")
            return True

        
        if command in ['unrole', 'унроль']:
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True

            my_role = await get_role(user_id, chat_id)

            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target_user = None
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])

            if not target_user:
                await message.replyLocalizedMessage('select_user')
                return True

            primary_owner_id = await _get_primary_chat_owner_id(chat_id)
            if int(target_user) == int(primary_owner_id):
                await message.reply("Нельзя снять роль у основного владельца беседы!", disable_mentions=1)
                return True

            if await _is_additional_chat_owner(target_user, chat_id):
                if not await _is_primary_chat_owner(user_id, chat_id):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
                sql.execute(
                    "DELETE FROM chat_coowners WHERE chat_id = ? AND user_id = ?",
                    (chat_id, target_user)
                )
                database.commit()

                role_name = await _get_custom_role_name(chat_id, 100)
                target_link = await format_vk_entity_link(target_user, chat_id)
                user_link = await format_vk_entity_link(user_id, chat_id)
                await message.reply(
                    f"{user_link} снял(-а) права {role_name} у {target_link}",
                    disable_mentions=1
                )
                await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"снял(-а) права «{role_name}» у пользователя")
                return True

            target_role = await get_role(target_user, chat_id)

            if await _is_modified_chat(chat_id):
                if target_role == 200:
                    await message.reply("Нельзя снять роль владельцу беседы!", disable_mentions=1)
                    return True
                if _role_hierarchy_value(target_role) >= _role_hierarchy_value(my_role) and target_role != 0:
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
            else:
                if _role_hierarchy_value(target_role) >= _role_hierarchy_value(my_role) and target_role != 0:
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

            target_link = await format_vk_entity_link(target_user, chat_id)
            user_link = await format_vk_entity_link(user_id, chat_id)

            if target_role == 0:
                await message.reply(
                    f"У {target_link} уже нет прав в данной беседе!",
                    disable_mentions=1
                )
                return True

            if await _is_modified_chat(chat_id):
                old_role_name = await _get_custom_role_name(chat_id, target_role)
            else:
                old_role_names = {1: "модератора", 2: "старшего модератора", 3: "администратора", 4: "старшего администратора", 5: "зам. спец администратора", 6: "спец. администратора"}
                old_role_name = old_role_names.get(target_role, f"уровень {target_role}")

            await roleG(target_user, chat_id, 0)
            await message.reply(
                f"{user_link} снял(-а) права {old_role_name} у {target_link}",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"снял(-а) права «{old_role_name}» у пользователя")
            return True

        
        if command in ['unsmsrole', 'унсмсроль']:
            if not await _is_modified_chat(chat_id):
                await message.reply("Сначала включите /mrole!", disable_mentions=1)
                return True

            my_role = await get_role(user_id, chat_id)

            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            target_user = None
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])

            if not target_user:
                await message.replyLocalizedMessage('select_user')
                return True

            target_role = await get_role(target_user, chat_id)

            if await _is_modified_chat(chat_id):
                if target_role == 200:
                    await message.reply("Нельзя снять роль владельцу беседы!", disable_mentions=1)
                    return True
                if _role_hierarchy_value(target_role) >= _role_hierarchy_value(my_role) and target_role != 0:
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True
            else:
                if _role_hierarchy_value(target_role) >= _role_hierarchy_value(my_role) and target_role != 0:
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

            if target_role == 0:
                target_link = await format_vk_entity_link(target_user, chat_id)
                await message.reply(
                    f"У {target_link} уже нет прав в беседах!",
                    disable_mentions=1
                )
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Пожалуйста подключите через /server", disable_mentions=1)
                return True

            for cid in server_chats:
                try:
                    await roleG(target_user, cid, 0)
                except Exception as e:
                    print(f"[UNSMSROLE] Ошибка при снятии роли в беседе {cid}: {e}")

            target_link = await format_vk_entity_link(target_user, chat_id)
            user_link = await format_vk_entity_link(user_id, chat_id)

            if await _is_modified_chat(chat_id):
                old_role_name = await _get_custom_role_name(chat_id, target_role)
            else:
                old_role_names = {1: "модератора", 2: "старшего модератора", 3: "администратора", 4: "старшего администратора", 5: "зам. спец администратора", 6: "спец. администратора"}
                old_role_name = old_role_names.get(target_role, f"уровень {target_role}")

            await message.reply(
                f"{user_link} снял(-а) права {old_role_name} у {target_link} в сетке бесед",
                disable_mentions=1
            )
            await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"снял(-а) права «{old_role_name}» в сетке бесед у пользователя")
            return True

        if command in ['mute', 'мут', 'мьют', 'муте', 'addmute']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 2
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 2
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 3
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if len(arguments) < 4 and arg == 3:
                await message.replyLocalizedMessage('command_mute_params')
                return True

            if len(arguments) < 3 and arg == 2:
                await message.replyLocalizedMessage('command_mute_params')
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_mute_preminisionss')
                return True

            if await get_mute(user, chat_id):
                await message.replyLocalizedMessage('command_mute_alyready')
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.replyLocalizedMessage('command_mute_not_reason')
                return True

            if arg == 3: mute_time = arguments[2]
            else: mute_time = arguments[1]
            try: mute_time = int(mute_time)
            except:
                await message.replyLocalizedMessage('command_mute_params')
                return True

            if mute_time < 1 or mute_time > 1000:
                await message.replyLocalizedMessage('command_mute_time')
                return True

            await add_mute(user, chat_id, user_id, reason, mute_time)
            await add_mutelog(chat_id, user, user_id, reason, mute_time, "выдан")

            do_time = datetime.now() + timedelta(minutes=mute_time)
            mute_time = str(do_time).split('.')[0]

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять мут", {"command": "unmute", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            await message.replyLocalizedMessage('command_mute', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'reason': reason,
                        'time_mute': mute_time
                    }, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"замутил(-а) @id{user} (пользователю). Мут выдан до: {mute_time}")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unmute', 'снятьмут', 'анмут', 'анмьют', 'унмут']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_unmute_preminisionss')
                return True

            if not await get_mute(user, chat_id):
                await message.replyLocalizedMessage('command_unmute_no')
                return True

            mute_info = await get_mute(user, chat_id)
            await unmute(user, chat_id)
            if mute_info:
                await add_mutelog(chat_id, user, user_id, mute_info['reason'], mute_info['time'], "снят")

            await message.replyLocalizedMessage('command_unmute', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'                       
                    })
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял(-а) мут @id{user} (пользователю)")           

        if command in ['getmute', 'gmute', 'гмут', 'гетмут', 'чекмут']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            await checkMute(chat_id, user)

            mute_string = str
            gmute = await get_mute(user, chat_id)
            if not gmute: mute_string = "У пользователя нет мута!"
            else:
                do_time = datetime.fromisoformat(gmute['date']) + timedelta(minutes=gmute['time'])
                mute_time = str(do_time).split('.')[0]

                try:
                    int(gmute['moder'])
                    mute_string = f"@id{gmute['moder']} (Модератор) | {gmute['reason']} | {gmute['date']} | До: {mute_time}"
                except: mute_string = f"Бот | {gmute['reason']} | {gmute['date']} | До: {mute_time}"

            await message.replyLocalizedMessage('command_getmute', {
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'info': mute_string
                    })
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"посмотрел(-а) историю мутов @id{user} (пользователя)")            

        if command in ['mutelist', 'mutes', 'муты', 'мутлист']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            mutes = await mutelist(chat_id)
            if not mutes: mutes_str = ""
            else:
                mutes_str = '\n'.join(mutes)

            await message.replyLocalizedMessage('command_mutelist', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'info': mutes_str
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список замученных в чате")

        if command in ['mutelogs', 'логимутов']:
            await mutelogs_command(message, arguments, user_id, chat_id, get_role, message.replyLocalizedMessage, getID, sql, datetime, timedelta, chats_log, get_user_name)

        if command in ['clear', 'чистка', 'очистить', 'очистка']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int            
            cmid = message.reply_message.conversation_message_id if message.reply_message else None
            user = message.reply_message.from_id if message.reply_message else None
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_clear_preminisionss')
                return True

            await message.replyLocalizedMessage('command_clear', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })
            
            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
            except: pass

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=message.conversation_message_id)
            except: pass            
            
        if command in ['deleteall', 'удалитьвсе']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_deleteall_preminisionss')
                return True

            
            
            try:
                sql.execute(
                    f"SELECT cmid FROM messages_{chat_id} "
                    "WHERE user_id = ? ORDER BY date DESC LIMIT 200",
                    (user,)
                )
                cmids = [row[0] for row in sql.fetchall() if row and row[0] is not None]
            except Exception as error:
                print(f"[deleteall] DB read error chat_id={chat_id} target_id={user}: {error}")
                await message.reply(
                    "Не удалось получить сохранённые сообщения пользователя.",
                    disable_mentions=1
                )
                return True

            if not cmids:
                await message.replyLocalizedMessage('command_deleteall_no_messages')
                return True

            
            
            
            deleted_count = 0
            delete_errors = []
            for start in range(0, len(cmids), 100):
                batch = cmids[start:start + 100]
                try:
                    await bot.api.messages.delete(
                        group_id=message.group_id,
                        peer_id=peer_id,
                        cmids=batch,
                        delete_for_all=True
                    )
                    deleted_count += len(batch)
                except Exception as error:
                    delete_errors.append(str(error))
                    print(f"[deleteall] delete error chat_id={chat_id} target_id={user}: {error}")

            if deleted_count <= 0:
                await message.reply(
                    "Не удалось удалить сообщения. Проверьте, что бот является администратором беседы и имеет право удалять сообщения.",
                    disable_mentions=1
                )
                return True

            try:
                placeholders = ",".join("?" for _ in cmids)
                sql.execute(
                    f"DELETE FROM messages_{chat_id} WHERE user_id = ? AND cmid IN ({placeholders})",
                    (user, *cmids)
                )
                database.commit()
            except Exception as error:
                print(f"[deleteall] DB cleanup error chat_id={chat_id} target_id={user}: {error}")

            await message.reply(
                f"Удалено {deleted_count} сообщений @id{user} ({await get_user_name(user, chat_id)}).",
                disable_mentions=1
            )
            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"удалил(-а) {deleted_count} сообщений @id{user} (пользователя)"
            )
            return True

        if command in ['mclear', 'мклиар']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете удалять сообщения этого пользователя!", disable_mentions=1)
                return True

            
            history = await bot.api.messages.get_history(
                peer_id=2000000000 + chat_id,
                count=500
            )

            
            cmids = [msg.conversation_message_id for msg in history.items if msg.from_id == user]

            if not cmids:
                await message.reply("У пользователя нет сообщений в последних 500.", disable_mentions=1)
                return True

            
            await bot.api.messages.delete(
                peer_id=2000000000 + chat_id,
                cmids=cmids,
                delete_for_all=True
            )

            await message.reply(f"Удалено {len(cmids)} сообщений @id{user} (пользователя)", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"удалил(-а) последнее 500 сообщений @id{user} (пользователя)")            
            return True            

        if command in ['alt', 'альт', 'альтернативные']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            commands_levels = {
                1: [
                    '\nКоманды модераторов:',
                    '/setnick — snick, nick, addnick, ник, сетник, аддник',
                    '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                    '/getnick — gnick, гник, гетник',
                    '/getacc — acc, гетакк, аккаунт, account',
                    '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                    '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                    '/kick — кик, исключить',
                    '/warn — пред, варн, pred, предупреждение',
                    '/unwarn — унварн, анварн, снятьпред, минуспред',
                    '/getwarn — gwarn, getwarns, гетварн, гварн',
                    '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                    '/warnlist — warns, wlist, варны, варнлист',
                    '/staff — стафф',
                    '/mute — мут, мьют, муте, addmute',
                    '/unmute — снятьмут, анмут, унмут, снятьмут',
                    '/alt — альт, альтернативные',
                    '/getmute -- gmute, гмут, гетмут, чекмут',
                    '/mutelist -- mutes, муты, мутлист',
                    '/clear -- чистка, очистить, очистка',
                    '/getban -- чекбан, гетбан, checkban',
                    '/delete -- удалить',
                    '/chatid -- чатайди, айдичата'
                ],
                2: [
                    '\nКоманды старших модераторов:',
                    '/ban — бан, блокировка',
                    '/unban -- унбан, снятьбан',
                    '/addmoder -- moder, модер',
                    '/removerole -- rrole, снятьроль',
                    '/zov - зов, вызов',
                    '/online - ozov, озов',
                    '/onlinelist - olist, олист',
                    '/banlist - bans, банлист, баны',
                    '/inactive - ilist, inactive'
                ],
                3: [
                    '\nКоманды администраторов:',
                    '/quiet -- silence, тишина',
                    '/skick -- скик, снят',
                    '/sban -- сбан',
                    '/sunban — сунбан, санбан',
                    '/addsenmoder — senmoder, смодер',
                    '/rnickall -- allrnick, arnick, mrnick',
                    '/sremovenick -- srnick',
                    '/szov -- serverzov, сзов',
                    '/srole -- prole, pullrole'
                ],
                4: [
                    '\nКоманды старших администраторов:',
                    '/addadmin -- admin, админ',
                    '/serverinfo -- серверинфо',
                    '/filter -- none',
                    '/sremoverole -- srrole',
                    '/ssetnick -- ssnick, сник',
                    '/bug -- баг',
                    '/report -- репорт, реп, rep, жалоба'
                ],
                5: [
                    '\nКоманды зам. спец администраторов:',
                    '/addsenadmin -- senadm, addsenadm, senadmin, садмин',
                    '/sync -- синхронизация, сунс, синхронка',
                    '/pin -- закрепить, пин',
                    '/unpin -- открепить, унпин',
                    '/deleteall -- удалитьвсе'
                    '/gsinfo -- none',
                    '/gsrnick -- none',
                    '/gssnick -- none',
                    '/gskick -- none',
                    '/gsban -- none',
                    '/gsunban -- none'
                ],
                6: [
                    '\nКоманды спец. администраторов:',
                    '/addzsa -- zsa, зса',
                    '/server -- сервер',
                    '/settings -- настройки',
                    '/clearwarn -- none',
                    '/title -- none',
                    '/antisliv -- антислив'
                ],
                7: [
                    '\nСписок команд владельца беседы',
                    '/addsa -- sa, са, spec, specadm',
                    '/antiflood -- af',
                    '/welcometext -- welcome, wtext',
                    '/invite -- none',
                    '/leave -- none',
                    '/server -- сервер',
                    '/editowner -- owner',
                    '/защита -- protection',
                    '/settingsmute -- настройкимута',
                    '/setinfo -- установитьинфо',
                    '/setrules -- установитьправила',
                    '/type -- тип',
                    '/gsync -- привязка',
                    '/gunsync -- удалитьпривязку',
                    '/masskick - mkick',
                    '/amnesty -- амнистия',
                    '/settingsgame -- настройкиигр',
                    '/settingsphoto -- настройкифото'
                ]
            }

            user_role = await get_role(user_id, chat_id)

            commands = []
            local_role_command_prefixes = (
                '/addmoder', '/removerole', '/addsenmoder', '/srole',
                '/addadmin', '/sremoverole', '/addsenadmin', '/addzsa', '/addsa'
            )
            is_modified_chat = await _is_modified_chat(chat_id)
            for i in commands_levels.keys():
                if _role_can_satisfy_standard_min(user_role, i):
                    for b in commands_levels[i]:
                        if is_modified_chat and any(b.lstrip().startswith(prefix) for prefix in local_role_command_prefixes):
                            continue
                        commands.append(b)

            level_commands = '\n'.join(commands)

            await message.reply(f"Альтернативные команды\n\n{level_commands}", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список альтернативных команд")            

        if command in ['pin', 'закрепить', 'пин']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            peer_id = chat_id + 2000000000

            if not message.reply_message:
                await message.replyLocalizedMessage('command_pin_replay')
                return True

            try:
                await bot.api.messages.pin(
                    peer_id=peer_id,
                    cmid=message.reply_message.conversation_message_id
                )
                await message.replyLocalizedMessage('command_pin')
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"закрепил(-а) сообщение в чате")            
            except Exception as e:
                await message.replyLocalizedMessage('command_pin_error', {
                        'error': e
                    })            
            return True
            
        if command in ['infobot', 'инфобот', 'информациябота']:
            await message.replyLocalizedMessage('command_infobot')
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список информации")            

        if command in ['q', 'выйти']:
            kick_user = user_id  
            try:
                peer_id_real = 2000000000 + chat_id  
                await bot.api.messages.remove_chat_user(chat_id, user_id)
                await message.replyLocalizedMessage('command_q', {
                        'user': userf
                    })
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"вышел(-а) из беседы")            
            except:
                await message.replyLocalizedMessage('command_q', {
                        'user': userf
                    })
                                
        if command in ['unpin', 'открепить', 'унпин']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            try:
                peer_id = chat_id + 2000000000
                await bot.api.messages.unpin(peer_id=peer_id)
                await message.replyLocalizedMessage('command_unpin')
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"открепил(-а) сообщение в чате")            
            except Exception as e:
                await message.replyLocalizedMessage('command_unpin_error', {
                        'error': e
                    })            
            return True
            
        if command in ['sync', 'синхронка', 'сунс']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            await message.replyLocalizedMessage('command_sync')
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"синхрозовал(-а) бота с базой данных")            
            return True
            
        if command in ['chatid', 'чатайди', 'айдичата']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            await message.replyLocalizedMessage('command_chatid', {
                        'id': chat_id
                    })
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) оригинальный айди беседы")            
            return True            

        if command in ['gbanpl', 'гбанпл', 'глобалбан']:
            if not await _has_role_level(user_id, chat_id, 10):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            target = int
            arg = 0
            if message.reply_message:
                target = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            sql.execute("SELECT * FROM gbanlist WHERE user_id = ?", (target,))
            check = sql.fetchone()
            if check:
                await message.reply("Данный пользователь уже имеет общую блокировку в беседах игроков!", disable_mentions=1)
                return True
                
            if await equals_roles(user_id, target, chat_id, message) < 2:
                await message.reply("Вы не можете выдать блокировку в беседах игроков данному пользователю!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            date_now = msk_now_str()

            sql.execute("INSERT INTO gbanlist (user_id, moderator_id, reason_gban, datetime_globalban) VALUES (?, ?, ?, ?)",
                        (target, user_id, reason, date_now))
            database.commit()

            
            try:
                await asyncio.gather(
                    log_admin_action(user_id, 'gbanpl'),
                    add_epoints_to_stat(user_id, 100)
                )
            except Exception as e:
                print(f"[EPOINTS] Ошибка начисления за gbanpl: {e}")

            
            sql.execute("SELECT chat_id FROM chats")
            chats = sql.fetchall()
            for chat_data in chats:
                chat_id_db = chat_data[0]
                try:
                    await bot.api.messages.remove_chat_user(chat_id_db, target)
                    await bot.api.messages.send(
                        peer_id=2000000000 + chat_id_db,
                        message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал в беседах игроков @id{target} ({await get_user_name(target, chat_id)})\nПричина: {reason}",
                        disable_mentions=1,
                        random_id=0
                    )
                except Exception as e:
                    print(f"Ошибка при обработке чата {chat_id_db}: {e}")
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), заблокировал(-а) в беседах игроков @id{target} ({await get_user_name(target, chat_id)})!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"заблокировал(-а) в беседах игроков @id{target} (пользователя). Причина: {reason}")            
            return True

        if command in ['gsync', 'привязка']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            linked = await get_gsync_chats(chat_id)
            if linked:
                await message.replyLocalizedMessage('command_gsync_alyready')
                return True

            
            sql.execute("SELECT table_name FROM gsync_list WHERE owner_id = ?", (user_id,))
            data = sql.fetchone()

            if not data:
                
                table_name = f"chats_gsync_{user_id}"
                sql.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (chat_id INTEGER, chat_title TEXT)")
                sql.execute("INSERT INTO gsync_list VALUES (?, ?)", (user_id, table_name))
                database.commit()
            else:
                table_name = data[0]

            
            try:
                resp = await bot.api.messages.get_conversations_by_id(peer_ids=2000000000 + chat_id)
                chat_title = resp.items[0].chat_settings.title if resp.items else "Без названия"
            except:
                chat_title = "Без названия"

            sql.execute(f"INSERT INTO {table_name} VALUES (?, ?)", (chat_id, chat_title))
            database.commit()

            await message.replyLocalizedMessage('command_gsync')
            return True
            
        if command in ['gunsync', 'удалитьпривязку']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            linked = await get_gsync_table(chat_id)
            if not linked:
                await message.replyLocalizedMessage('command_gunsync_none_privazka')
                return True

            table_name = linked["table"]

            sql.execute(f"DELETE FROM {table_name} WHERE chat_id = ?", (chat_id,))
            database.commit()

            await message.replyLocalizedMessage('command_gunsync')
            return True

        if command in ['gsinfo', 'гсинфо']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            gsync_data = await get_gsync_table(chat_id)
            if not gsync_data:
                await message.replyLocalizedMessage('command_gsinfo_none')
                return True

            table_name = gsync_data["table"]
            sql.execute(f"SELECT chat_title FROM {table_name}")
            chats = sql.fetchall()

            chats_text = ""
            i = 1
            for c in chats:
                chats_text += f"{i}. {c[0]}\n"
                i += 1

            await message.reply(
                f"📌 Информация о глобальной привязки беседы:\n"
                f"1️⃣ Количество бесед в глобальной связке: {len(chats)}\n"
                f"2️⃣ Список бесед в привязке:\n{chats_text}",
                disable_mentions=1
            )
            return True            

        if command in ['gunbanpl', 'гунбанпл', 'ungbanpl']:
            if not await _has_role_level(user_id, chat_id, 10):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == 89:
                await message.replyLocalizedMessage('testers_chat')
                return True

            target = int
            if message.reply_message:
                target = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True
               
            if await equals_roles(user_id, target, chat_id, message) < 2:
                await message.reply("Вы не можете разблокировать данного пользователя!", disable_mentions=1)
                return True

            sql.execute("SELECT * FROM gbanlist WHERE user_id = ?", (target,))
            check = sql.fetchone()
            if not check:
                await message.reply("Данный пользователь не имеет общей блокировки в беседах игроков!", disable_mentions=1)
                return True

            sql.execute("DELETE FROM gbanlist WHERE user_id = ?", (target,))
            database.commit()

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), разблокировал(-а) в беседах игроков @id{target} ({await get_user_name(target, chat_id)})!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"разблокировал(-а) @id{target} (пользователя) в беседах игроков")                
            return True


        if command in ['gban', 'гбан']:
            if not await _has_role_level(user_id, chat_id, 10):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            target = int
            arg = 0
            if message.reply_message:
                target = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            sql.execute("SELECT * FROM globalban WHERE user_id = ?", (target,))
            check = sql.fetchone()
            if check:
                await message.reply("Данный пользователь уже имеет общую блокировку в чатах!", disable_mentions=1)
                return True
                
            if await equals_roles(user_id, target, chat_id, message) < 2:
                await message.reply("Вы не можете выдать общую блокировку в чатах данному пользователю!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            date_now = msk_now_str()

            sql.execute("INSERT INTO globalban (user_id, moderator_id, reason_gban, datetime_globalban) VALUES (?, ?, ?, ?)",
                        (target, user_id, reason, date_now))
            database.commit()

            
            try:
                await asyncio.gather(
                    log_admin_action(user_id, 'gban'),
                    add_epoints_to_stat(user_id, 50)
                )
            except Exception as e:
                print(f"[EPOINTS] Ошибка начисления за gban: {e}")

            
            sql.execute("SELECT chat_id FROM chats")
            chats = sql.fetchall()
            for chat_data in chats:
                chat_id_db = chat_data[0]
                try:
                    
                    sql.execute("SELECT type FROM chats WHERE chat_id = ?", (chat_id_db,))
                    chat_type_result = sql.fetchone()
                    if chat_type_result and is_special_gban_chat_type(chat_type_result[0]):
                        await bot.api.messages.remove_chat_user(chat_id_db, target)
                        await bot.api.messages.send(
                            peer_id=2000000000 + chat_id_db,
                            message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал в чатах @id{target} ({await get_user_name(target, chat_id)})\nПричина: {reason}",
                            disable_mentions=1,
                            random_id=0
                        )
                except Exception as e:
                    print(f"Ошибка при обработке чата {chat_id_db}: {e}")
                    continue

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), заблокировал(-а) в чатах @id{target} ({await get_user_name(target, chat_id)})!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"заблокировал(-а) в чатах @id{target} (пользователя). Причина: {reason}")            
            return True


        if command in ['gunban', 'ungban']:
            if not await _has_role_level(user_id, chat_id, 10):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            target = int
            if message.reply_message:
                target = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, target, chat_id, message) < 2:
                await message.reply("Вы не можете разблокировать данного пользователя!", disable_mentions=1)
                return True

            sql.execute("SELECT * FROM globalban WHERE user_id = ?", (target,))
            check = sql.fetchone()
            if not check:
                await message.reply("Данный пользователь не имеет общей блокировки в чатах!", disable_mentions=1)
                return True

            sql.execute("DELETE FROM globalban WHERE user_id = ?", (target,))
            database.commit()

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), разблокировал(-а) в чатах @id{target} ({await get_user_name(target, chat_id)})!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=target, role=None, log=f"разблокировал(-а) @id{target} (пользователя) в чатах")                
            return True
            
        if command in ['report', 'репорт', 'жалоба']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.replyLocalizedMessage('command_report_reason')
                return True

            try:
                u_name = await get_user_name(user, chat_id)
                s_name = await get_user_name(user_id, chat_id)
            except:
                u_name = str(user)
                s_name = str(user_id)

            
            admin_ids = [488828183, 574393629]
            report_text = (
                f"@all (Внимание), @all (Внимание)\n"
                f"❗ | Новая жалоба на пользователя!\n\n"
                f"👤 | Отправитель: @id{user_id} ({s_name})\n"
                f"🚫 | Жалоба на: @id{user} ({u_name})\n"
                f"💬 | Причина: {reason}\n"
                f"💭 | Беседа: ID {chat_id}"
            )

            try:
                for admin_id in admin_ids:
                    await bot.api.messages.send(
                        peer_id=admin_id,
                        message=report_text,
                        random_id=0
                    )
                await chats_log(user_id=user_id, target_id=user, role=None, log=f"подал(-а) репорт на @id{user} (пользователя). Причина: {reason}")            
                await message.replyLocalizedMessage('command_report', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'reason': reason
                    })            
            except Exception as e:
                await message.reply(f"⚠️ Ошибка при отправке жалобы.\n\nВк говорит: {e}", disable_mentions=1)
                print(f"[report command] Ошибка отправки админу: {e}")

            return True            
 
        if command in ["infochat", "инфочат"]:
                if not await _has_role_level(user_id, chat_id, 11):
                    await message.replyLocalizedMessage('not_preminisionss')
                    return True

                if len(arguments) < 2:
                    await message.reply("Использование: /infochat 12")
                    return True

                try:
                    chat_target = int(arguments[1])
                    peer_id = 2000000000 + chat_target
                except:
                    await message.reply("Неверный ID беседы!")
                    return True

                try:
                    
                    response = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
                    if not response.items:
                        await message.reply("Беседа не найдена!")
                        return True

                    chat_data = response.items[0]
                    chat_settings = chat_data.chat_settings
                    title = chat_settings.title if chat_settings.title else "Без названия"
                    peoples = chat_settings.members_count or 0
                    active_ids = chat_settings.active_ids or []
                except Exception as e:
                    print(f"[INFOCHAT] Ошибка при получении информации: {e}")
                    title = (await get_chat_display_info(chat_target))[0]
                    peoples = "Не удалось получить"
                    active_ids = []

                try:
                    sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_target,))
                    chat_db = sql.fetchone()
                    owner_id = chat_db[0] if chat_db else "Не удалось получить"
                except Exception as e:
                    print(f"[INFOCHAT] Ошибка при обращении к БД: {e}")
                    owner_id = "Не удалось получить"

                
                chat_title_cached, chat_link_cached = await get_chat_display_info(chat_target)
                if title in {"Не удалось получить", "Без названия", None, ""}:
                    title = chat_title_cached
                link = format_chat_link_text(chat_link_cached)

                
                all_peoples = ""
                all_admins = ""
                try:
                    members = await bot.api.messages.get_conversation_members(peer_id=peer_id)
                    all_users = members.profiles
                    all_admin_ids = [x.member_id for x in members.items if getattr(x, "is_admin", False)]

                    i = 1
                    for user in all_users:
                        all_peoples += f"{i}. @id{user.id} ({user.first_name} {user.last_name})\n"
                        i += 1

                    admins_count = len(all_admin_ids)
                    j = 1
                    for uid in all_admin_ids:
                        all_admins += f"{j}. @id{uid} ({await get_user_name(uid, chat_id)})\n"
                        j += 1

                except Exception as e:
                    print(f"[INFOCHAT] Ошибка при получении участников: {e}")
                    all_peoples = "Не удалось получить"
                    all_admins = "Не удалось получить"
                    admins_count = "Не удалось получить"

                
                status = "🟢 Чат активен и успешно работает"

                
                text = (
                    f"📋 Информация о беседе №{chat_target}\n\n"
                    f"👑 Владелец беседы: @id{owner_id} ({await get_user_name(owner_id, chat_id)})\n"
                    f"💬 Название чата: {title}\n"
                    f"👥 Количество участников: {peoples}\n"
                    f"📃 Из них:\n{all_peoples}\n"
                    f"🛡 Количество администраторов: {admins_count}\n"
                    f"📃 Из них:\n{all_admins}\n"
                    f"🔗 {link}\n"
                    f"⚙️ Статус беседы: {status}"
                )

                await message.reply(text, disable_mentions=1)
                return True                
           
        if command in ['listchats', 'листчатов', 'списокбесед', 'чаты']:
                if not await _has_role_level(user_id, chat_id, 11):
                        await message.replyLocalizedMessage('not_preminisionss')
                        return True

                all_rows, inactive_rows = await get_active_registered_chats()
                if not all_rows:
                        await message.reply("Список активных чатов пуст!", disable_mentions=1)
                        return True

                per_page = 20

                page = 1
                chats_text, max_page = await build_chats_registry_page(all_rows, page, per_page)
                hidden_text = f"\n\nСкрыто недоступных бесед: {len(inactive_rows)}" if inactive_rows else ""

                keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("⏪", {"command": "chatsMinus", "page": 1}), color=KeyboardButtonColor.NEGATIVE)
                        .add(Callback("⏩", {"command": "chatsPlus", "page": 1}), color=KeyboardButtonColor.POSITIVE)
                )

                await message.reply(
                        f"Список зарегистрированных чатов [1/{max_page} страница]:\n\n"
                        f"{chats_text}{hidden_text}",
                        disable_mentions=1, keyboard=keyboard
                )
                await chats_log(user_id=user_id, target_id=None, role=None, log="посмотрел(-а) список зарегистрированных бесед")
                return True                

        if command in ['title']:
            if not await _can_use_command(user_id, chat_id, 6):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            if len(arguments) < 2:
                await message.replyLocalizedMessage('command_title_params')
                return True

            new_title = " ".join(arguments[1:])
            try:
                await bot.api.messages.edit_chat(chat_id=chat_id, title=new_title)
                await message.replyLocalizedMessage('command_title', {
                        'title': new_title
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"изменил(-а) название чата на {new_title}")            
            except Exception as e:
                await message.replyLocalizedMessage('command_title_error', {
                        'error': e
                    })            
            return True
                       
        if command in ['ban', 'бан', 'блокировка']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('command_ban_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.replyLocalizedMessage('command_ban_not_reason')
                return True

            if await checkban(user, chat_id):
                await message.replyLocalizedMessage('command_ban_alyready')
                return True

            await ban(user, user_id, chat_id, reason)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except: pass

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            await message.replyLocalizedMessage('command_ban', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})',
                        'reason': reason
                    }, keyboard=keyboard)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"заблокировал(-а) @id{user} (пользователя). Причина: {reason}")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['сразраб', 'разраб', 'разработчик']:
            
            allowed_ids = [488828183, 574393629]  

            if user_id not in allowed_ids:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            await globalrole(user_id, 7)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"выдал(-а) себе права разработчика бота")            
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) себе права разработчика бота!",
                disable_mentions=1
            )
            return True 

        if command in ['кк']:
            
            allowed_ids = [488828183, 870478120, 574393629]

            if user_id not in allowed_ids:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            await globalrole(user_id, GLOBAL_LEVEL_QUALITY_CONTROL)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"выдал(-а) себе роль Контроля качества")
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) себе роль Контроля качества!",
                disable_mentions=1
            )
            return True 
        
        if command in ['manager', 'director', 'рук']:
            
            allowed_ids = [488828183,574393629]  

            if user_id not in allowed_ids:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            await globalrole(user_id, 6)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"выдал(-а) себе права директора бота")            
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) себе права директора бота!",
                disable_mentions=1
            )
            return True
            
        if command in ['свладелец', 'владельцас', 'ownerme']:
            
            allowed_ids = [488828183, 574393629]

            if user_id not in allowed_ids:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            await set_onwer(user_id, chat_id)
            await roleG(user_id, chat_id, 0)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"выдал(-а) себе права разработчика бота")            
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) себе права владельца беседы!",
                disable_mentions=1
            )
            return True                 

        if command in ['снятьразработчика', 'снятьразраба', 'deldev', 'оффроль']:
            
            grrole_ids = [488828183, 870478120, 574393629]
            
            
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
            global_role_fetch = sql.fetchone()
            has_dev_role = global_role_fetch and int(global_role_fetch[0]) == 7
            has_quality_control_role = global_role_fetch and int(global_role_fetch[0]) == GLOBAL_LEVEL_QUALITY_CONTROL
            
            
            if user_id not in grrole_ids and not has_dev_role and not has_quality_control_role:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                
                await globalrole(user_id, 0)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"снял(-а) с себя глобальную роль")            
                await message.reply(
                    f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) с себя глобальную роль!",
                    disable_mentions=1
                )
                return True

            
            if user in grrole_ids and user_id in grrole_ids:
                await message.reply("Вы не можете снять роль другому grrole пользователю!", disable_mentions=1)
                return True

            await globalrole(user, 0)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял(-а) глобальную роль с @id{user} (пользователя)")            
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) глобальную роль у @id{user} ({await get_user_name(user, chat_id)})!",
                disable_mentions=1
            )
            return True
            
        if command in ['unban', 'унбан', 'снятьбан']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            getban = await checkban(user, chat_id)
            if not getban:
                await message.replyLocalizedMessage('command_unban_not')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 1:
                await message.replyLocalizedMessage('command_unban_preminisionss')
                return True

            await unban(user, chat_id)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"разблокировал(-а) @id{user} (пользователя) в беседе.")            
            await message.replyLocalizedMessage('command_unban', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            

        if command in ['addmoder', 'moder','модер']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 1):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addmoder', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права модератора @id{user} (пользователю)")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['removerole', 'rrole', 'снятьроль']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 0)
            await message.replyLocalizedMessage('command_removerole', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял(-а) права с @id{user} (пользователя)")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['grrole', 'globalrrole', 'гснятьроль']:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            special_grrole_ids = [488828183, 574393629]
            if user_id in special_grrole_ids and user in special_grrole_ids:
                await message.reply("Вы не можете снять роль другому grrole пользователю!", disable_mentions=1)
                return True

            
            
            if user_id not in special_grrole_ids:
                if await equals_roles(user_id, user, chat_id, message) < 2:
                    await message.reply("Вы не можете снять роль данному пользователю!", disable_mentions=1)
                    return True

            await globalrole(user, 0)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) глобальную роль у @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"снял(-а) глобальную роль с @id{user} (пользователя)")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['снятьрольнавсегда', 'adminrrole', 'arrole']:
            allowed_id = 488828183,574393629

            if user_id != allowed_id:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            await globalrole(user, 0)
            await roleG(user_id, chat_id, 0)
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) роль во всех чатах у "
                f"@id{user} ({await get_user_name(user, chat_id)})",
                disable_mentions=1
            )

            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"снял(-а) глобальную роль с @id{user} (пользователя)"
            )

            await add_punishment(chat_id, user_id)

            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений "
                    f"в сливе беседы\n\n{await staff_zov(chat_id)}"
                )

        if command in ['zov', 'зов', 'вызов']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, 1)
            if not reason:
                await message.replyLocalizedMessage('command_zov_not_reason')
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.model_dump_json())
            user_f = []
            gi = 0
            for i in users["profiles"]:
                if not i['id'] == user_id:
                    gi = gi + 1
                    if gi <= 100:
                        user_f.append(f"@id{i['id']} (🖤)")
            zov_users = ''.join(user_f)

            await message.replyLocalizedMessage('command_zov', {
                        'user': f'@id{user_id} (администратором беседы)',
                        'zov_users': zov_users,
                        'reason': reason
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"вызвал(-а) всех пользователей в беседе. Причина: {reason}")
            return True

        if command in ['ozov', 'online', 'озов']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, 1)
            if not reason:
                await message.replyLocalizedMessage('command_ozov_not_reason')
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.model_dump_json())
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 100:
                            online_users.append(f"@id{i['id']} (♦️)")

            online_zov = "".join(online_users)
            await message.replyLocalizedMessage('command_ozov', {
                        'user': f'@id{user_id} (администратором беседы)',
                        'info': online_zov,
                        'reason': reason
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"вызвал(-а) всех пользователей онлайн в беседе. Причина: {reason}")            

        if command in ['onlinelist', 'olist', 'олист']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online", "online_info"])
            users = json.loads(users.model_dump_json())
            
            member_ids = {item.get("member_id") for item in users.get("items", [])}
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["id"] not in member_ids:
                    continue
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 80:
                            if i["online_info"]["is_mobile"] == False:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 💻")
                            else:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 📱")

            olist_users = "\n".join(online_users)
            await message.replyLocalizedMessage('command_onlinelist', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'info': online_users,
                        'count': gi
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список пользователей онлайн в чате")            

        if command in ['banlist', 'bans', 'банлист', 'баны']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            bans = await banlist(chat_id)
            bans_do = []
            gi = 0
            for i in bans:
                gi = gi + 1
                if gi <= 10:
                    bans_do.append(i)
            bans_str = "\n".join(bans_do)

            await message.replyLocalizedMessage('command_banlist', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'info': bans_str,
                        'count': gi
                    })
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список заблокированных пользователей в чате")            

        if command in ['delete', 'удалить']:
            if not await _can_use_command(user_id, chat_id, 1):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if not message.reply_message:
                await message.reply("Чтобы удалить сообщение, нужно ответить на него!")
                return True

            cmid = message.reply_message.conversation_message_id
            user = message.reply_message.from_id

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете удалить сообщение данного пользователя!", disable_mentions=1)
                return True

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
            except: pass

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=message.conversation_message_id)
            except: pass


        
        
        if command in ['afchat', 'афчат']:
            if not await _has_role_level(user_id, chat_id, 17):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                new_chat_id = chat_id
            else:
                try:
                    new_chat_id = int(arguments[1])
                except:
                    await message.reply("Неверный формат ID чата!", disable_mentions=1)
                    return True

            sql.execute("INSERT OR REPLACE INTO af_config (id, chat_id) VALUES (1, ?)", (new_chat_id,))
            database.commit()
            await message.reply(f"Чат для приёма форм назначен: {new_chat_id}\nТеперь все формы из `/af` будут приходить туда.", disable_mentions=1)
            return True

        
        if command in ['af', 'аф']:
            args_text = message.text.strip()
            parts = args_text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await message.reply(
                    "📋 Использование: /af /gbanpl @user Имя Фамилия\n\n"
                    "Примеры:\n"
                    "• /af /gbanpl @id1 Иван Иванов\n"
                    "• /af /gban @id1 Иван Иванов\n\n"
                    "⚠️ Обязательно прикрепите хотя бы 1 фото или видео как доказательство!",
                    disable_mentions=1
                )
                return True

            form_text = parts[1].strip()

            media_attachments = extract_message_media_attachments(message)
            if not media_attachments:
                await message.reply("❌ К форме обязательно нужно прикрепить хотя бы 1 фото или видео как доказательство!", disable_mentions=1)
                return True

            try:
                user_info = await bot.api.users.get(user_ids=user_id)
                user_name = f"{user_info[0].first_name} {user_info[0].last_name}"
            except:
                user_name = str(user_id)

            form_type = ""
            form_reason = ""
            target_user_id = 0
            target_user_name = ""
            form_words = form_text.split()
            if form_words:
                form_type = form_words[0]
                rest = " ".join(form_words[1:])
                vk_match = re.search(r'(?:https?://)?(?:m\.)?vk\.(?:com|ru)/([\w.]+)', rest, flags=re.IGNORECASE)
                id_match = re.search(r'@id(\d+)', rest)
                text_target_id = extract_user_id_from_text(rest)
                if text_target_id:
                    target_user_id = int(text_target_id)
                    rest = remove_user_reference_from_text(rest, target_user_id)
                elif id_match:
                    target_user_id = int(id_match.group(1))
                    rest = remove_user_reference_from_text(rest, target_user_id)
                elif vk_match:
                    username = vk_match.group(1)
                    try:
                        resolve = await bot.api.users.get(user_ids=username)
                        if resolve:
                            target_user_id = resolve[0].id
                    except:
                        pass
                    rest = re.sub(
                        rf'(?:https?://)?(?:m\.)?vk\.(?:com|ru)/{re.escape(username)}\b/?',
                        ' ',
                        rest,
                        flags=re.IGNORECASE,
                    ).strip()
                    if target_user_id > 0:
                        rest = remove_user_reference_from_text(rest, target_user_id)
                elif message.reply_message and message.reply_message.from_id > 0:
                    target_user_id = message.reply_message.from_id
                    rest = remove_user_reference_from_text(rest, target_user_id)
                if target_user_id > 0:
                    rest = remove_user_reference_from_text(rest, target_user_id)
                    try:
                        t_info = await bot.api.users.get(user_ids=target_user_id)
                        target_user_name = f"{t_info[0].first_name} {t_info[0].last_name}"
                    except:
                        target_user_name = str(target_user_id)
                form_reason = rest if rest else "Не указана"

            reply_text = f"[id{user_id}|{user_name}], Мы рассмотрим вашу форму, ожидайте!"
            attach_str = ",".join(media_attachments)
            await message.reply(reply_text, disable_mentions=1)

            sql.execute("SELECT chat_id FROM af_config WHERE id = 1")
            af_row = sql.fetchone()
            if af_row:
                af_chat_id = af_row[0]
                target_link = f"[id{target_user_id}|{target_user_name}]" if target_user_id > 0 else "Не указан"
                form_msg = (
                    f"📋 Новая форма, Руководство реакция!\n\n"
                    f"Отправитель: [id{user_id}|{user_name}]\n"
                    f"Тип: {form_type}\n"
                    f"Причина: {form_reason}\n"
                    f"Наказуемый: {target_link}\n\n"
                    f"Просьба рассмотреть форму и выдать наказание за нарушение правил бота!"
                )
                try:
                    if attach_str:
                        await bot.api.messages.send(
                            peer_id=2000000000 + af_chat_id,
                            message=form_msg,
                            attachment=attach_str,
                            random_id=0,
                            disable_mentions=1
                        )
                    else:
                        await bot.api.messages.send(
                            peer_id=2000000000 + af_chat_id,
                            message=form_msg,
                            random_id=0,
                            disable_mentions=1
                        )
                except Exception as e:
                    print(f"[AF] Ошибка отправки в чат форм {af_chat_id}: {e}")

            await increment_forms_submitted(user_id)
            await _recalculate_admin_norm_for_user(user_id, chat_id)
            return True

        
        if command in ['aфтехчат', 'афтехчат', 'aфтехchat', 'афтехchat']:
            if not await _has_role_level(user_id, chat_id, 17):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                
                sql.execute("SELECT chat_id FROM af_tech_config WHERE id = 1")
                row = sql.fetchone()
                if row:
                    await message.reply(f"Чат для приёма тех. форм назначен: {row[0]}\nИспользование: /афтехчат ID_чата", disable_mentions=1)
                else:
                    await message.reply("Чат для приёма тех. форм не назначен.\nИспользование: /афтехчат ID_чата", disable_mentions=1)
                return True
            
            try:
                new_chat_id = int(arguments[1])
            except:
                await message.reply("Неверный формат ID чата!", disable_mentions=1)
                return True

            sql.execute("INSERT OR REPLACE INTO af_tech_config (id, chat_id) VALUES (1, ?)", (new_chat_id,))
            database.commit()
            await message.reply(f"Чат для приёма тех. форм назначен: {new_chat_id}\nТеперь все заявки из `/афтех` будут приходить туда.", disable_mentions=1)
            return True

        
        if command in ['aфтех', 'афтех', 'aфтехform', 'афтехform']:
            
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 9):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            
            try:
                user_info = await bot.api.users.get(user_ids=user_id)
                user_name = f"{user_info[0].first_name} {user_info[0].last_name}"
            except:
                user_name = str(user_id)
            
            
            target_user_id = None
            if message.reply_message:
                target_user_id = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user_id = await getID(arguments[1])
            else:
                try:
                    await bot.api.messages.edit(
                        peer_id=message.peer_id,
                        message=(
                            "📋 Заявка на обнуление/восстановление\n\n"
                            "Укажите пользователя:\n"
                            "• Ответьте сообщением на пользователя\n"
                            "• Или введите ID: /афтех @user\n\n"
                            "После этого выберите тип имущества."
                        ),
                        conversation_message_id=message.conversation_message_id,
                        disable_mentions=1
                    )
                except:
                    await message.reply(
                        "📋 Заявка на обнуление/восстановление\n\n"
                        "Укажите пользователя:\n"
                        "• Ответьте сообщением на пользователя\n"
                        "• Или введите ID: /афтех @user\n\n"
                        "После этого выберите тип имущества.",
                        disable_mentions=1
                    )
                return True
            
            if not target_user_id:
                await message.reply("Не удалось определить пользователя!", disable_mentions=1)
                return True
            
            try:
                target_info = await bot.api.users.get(user_ids=target_user_id)
                target_user_name = f"{target_info[0].first_name} {target_info[0].last_name}"
            except:
                target_user_name = str(target_user_id)
            
            
            kb = Keyboard(inline=True)
            for i, (ptype, pname) in enumerate(RESET_PROPERTY_TYPES):
                kb.add(Callback(pname, {"command": "aфтех_select", "target": target_user_id, "target_name": target_user_name, "ptype": ptype, "owner_id": user_id}))
                if (i + 1) % 3 == 0:
                    kb.row()
            kb.row()
            kb.add(Callback("🔄 Обнулить всё", {"command": "aфtech_proceed", "target": target_user_id, "target_name": target_user_name, "ptype": "all", "owner_id": user_id}))
            
            try:
                await bot.api.messages.edit(
                    peer_id=message.peer_id,
                    message=(
                        f"📋 Заявка на обнуление/восстановление\n\n"
                        f"👤 Цель: [id{target_user_id}|{target_user_name}]\n\n"
                        f"Выберите тип имущества:"
                    ),
                    conversation_message_id=message.conversation_message_id,
                    keyboard=kb,
                    disable_mentions=1
                )
            except:
                await message.reply(
                    f"📋 Заявка на обнуление/восстановление\n\n"
                    f"👤 Цель: [id{target_user_id}|{target_user_name}]\n\n"
                    f"Выберите тип имущества:",
                    keyboard=kb,
                    disable_mentions=1
                )
            return True

        
        if command in ['aфтехлист', 'афтехлист', 'aфтехlist', 'афтехlist', 'aфлист', 'афлист', 'aflist', 'аflist']:
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 12):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            page = 0
            if len(arguments) >= 2 and arguments[1].isdigit():
                page = int(arguments[1])
            pending_requests = await get_pending_reset_requests("pending")

            if not pending_requests:
                await message.reply("Нет активных заявок на обнуление.", disable_mentions=1)
                return True

            per_page = 5
            total = len(pending_requests)
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = max(0, min(page, total_pages - 1))
            page_items = pending_requests[page * per_page:(page + 1) * per_page]

            kb = Keyboard(inline=True)
            for req in page_items:
                tname = str(req.get('target_user_name', '??'))[:15]
                label = f"Обнуление - {tname}"
                if len(label) > 40:
                    label = label[:37] + "..."
                kb.add(Callback(label, {"command": "aфtech_view", "request_id": req['request_id']}))

            kb.row()
            nav_parts = []
            if page > 0:
                nav_parts.append(Callback("◀️", {"command": "aфтехлист", "page": page - 1}))
            if total_pages > 1:
                nav_parts.append(Callback(f"{page+1}/{total_pages}", {"command": "aфtechlist_nop"}))
            if page < total_pages - 1:
                nav_parts.append(Callback("▶️", {"command": "aфтехлист", "page": page + 1}))
            for btn in nav_parts:
                kb.add(btn)

            kb.row()
            kb.add(Callback("🔄", {"command": "aфтехлист", "page": 0}))

            await message.reply(
                f"📋 Заявки на обнуление ({total} шт.)\n"
                f"Выберите заявку:",
                keyboard=kb,
                disable_mentions=1
            )
            return True


        
        if command in ['astats', 'астатс']:
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 9):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            target_user_id = user_id
            if message.reply_message:
                target_user_id = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user_id = await getID(arguments[1])
            
            target_role = await get_role(target_user_id, chat_id)
            target_role_level = _effective_standard_role_level(target_role)
            if target_user_id != user_id and _role_hierarchy_value(target_role) >= _role_hierarchy_value(role):
                await message.reply("Вы не можете смотреть статистику администратора с более высоким или равным уровнем!", disable_mentions=1)
                return True
            
            try:
                target_info = await bot.api.users.get(user_ids=target_user_id)
                target_name = f"{target_info[0].first_name} {target_info[0].last_name}"
            except:
                target_name = str(target_user_id)
            
            total_ep = await get_total_epoints(target_user_id)
            
            stats_days = await _load_admin_stats_days(target_user_id, 7)
            
            page = 0
            start = page * 3
            end = min(start + 3, 7)
            page_days = stats_days[start:end]
            
            days_text = "".join(_format_admin_stats_day_line(s, target_role_level) for s in page_days)
            
            kb = Keyboard(inline=True)
            for s in page_days:
                kb.add(Callback(s['stat_date'], {"command": "astats_day", "user": target_user_id, "date": s['stat_date'], "owner_id": user_id}))
                kb.row()
            kb.add(Callback("Статистика за неделю", {"command": "astats_week", "user": target_user_id, "owner_id": user_id}))
            kb.row()
            kb.add(Callback("Статистика за всё время", {"command": "astats_all", "user": target_user_id, "owner_id": user_id}))
            kb.row()
            kb.add(Callback("Вперед ▶️", {"command": "astats_page", "user": target_user_id, "page": 1, "owner_id": user_id}), color=KeyboardButtonColor.SECONDARY)
            
            detail_title = _admin_stats_title(target_role_level)
            
            stats_msg = (
                f"{detail_title} - [id{target_user_id}|{target_name}]\n"
                f"Epoints - {format_number(total_ep)} баллов\n\n"
                f"{days_text.rstrip()}"
            )
            
            await message.answer(stats_msg, keyboard=kb, disable_mentions=1)
            return True
        
        
        
        
        
        if command in ['epoints', 'епоинтс']:
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 9):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            total_ep = await get_total_epoints(user_id)
            ep_shop_inputs.pop(user_id, None)
            
            ep_text = f"💎 Баланс: {format_number(total_ep)} Epoints\n\nВыберите категорию:"
            
            kb = Keyboard(inline=True)
            kb.add(Callback("💱 Валюты", {"command": "ep_shop", "cat": "money", "owner_id": user_id}))
            kb.add(Callback("🍌 Коины", {"command": "ep_shop", "cat": "bananas", "owner_id": user_id}))
            kb.row()
            kb.add(Callback("🏢 Бизнесы", {"command": "ep_shop", "cat": "business", "owner_id": user_id}))
            kb.add(Callback("📦 Кейсы", {"command": "ep_shop", "cat": "case", "owner_id": user_id}))
            kb.row()
            kb.add(Callback("🎁 Предметы", {"command": "ep_shop", "cat": "item", "owner_id": user_id}))
            kb.add(Callback("⭐ VIP", {"command": "ep_shop", "cat": "vip", "owner_id": user_id}))
            
            await message.answer(ep_text, keyboard=kb, disable_mentions=1)
            return True
        
        
        if command in ['norm', 'норма']:
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 9):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            target_user_id = user_id
            if message.reply_message:
                target_user_id = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user_id = await getID(arguments[1])
            
            target_role_raw = await get_role(target_user_id, chat_id)
            target_role = _effective_standard_role_level(target_role_raw)
            role_level = _effective_standard_role_level(role)
            if target_user_id != user_id and _role_hierarchy_value(target_role_raw) >= _role_hierarchy_value(role):
                await message.reply("Вы не можете смотреть норму вышестоящего!", disable_mentions=1)
                return True
            
            try:
                target_info = await bot.api.users.get(user_ids=target_user_id)
                target_name = f"{target_info[0].first_name} {target_info[0].last_name}"
            except:
                target_name = str(target_user_id)
            
            today_str = msk_now().strftime("%Y-%m-%d")
            norm_status, epoints_reward = await calculate_and_set_norm(target_user_id, target_role, today_str)
            
            status_map = {'completed': '✅ Выполнена', 'stretched': '⚠️ Натянута', 'not_completed': '❌ Норма не выполнена', 'no_norm': '❌ Нет нормы'}
            status_text = status_map.get(norm_status, norm_status)
            
            role_names = {
                9: "Технический специалист",
                10: "Заместитель директора",
                11: "Основной заместитель директора",
                12: "Куратор техспециалистов",
                13: "Заместитель Главного Тех. Специалиста",
                14: "Главный Тех. Специалист",
                15: "Директор бота",
                16: GLOBAL_QUALITY_CONTROL_NAME,
                17: "Разработчик бота"
            }
            target_role_name = role_names.get(target_role, f"Роль {target_role}")
            
            norm_text = f"📋 Норма - [id{target_user_id}|{target_name}]\n"
            norm_text += f"Роль: {target_role_name}\n"
            norm_text += f"Статус: {status_text}\n\n"
            norm_text += await _build_admin_norm_progress_text(target_user_id, target_role, today_str)
            norm_text += "\n\n"
            
            if target_role == 9:
                norm_text += "Норма Технического специалиста:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Отправлено форм: ≥2\n"
                norm_text += "  • Сообщений: ≥100\n"
                norm_text += f"  • Награда: 100 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Отправлено форм: ≥1\n"
                norm_text += "  • Сообщений: ≥50\n"
                norm_text += f"  • Награда: 50 Epoints\n"
            
            elif target_role == 10:
                norm_text += "Норма Заместителя директора:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Сообщений: ≥600\n"
                norm_text += f"  • Награда: 300 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Сообщений: ≥300\n"
                norm_text += f"  • Награда: 150 Epoints\n"
            
            elif target_role == 11:
                norm_text += "Норма Основного заместителя директора:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Сообщений: ≥400\n"
                norm_text += f"  • Награда: 400 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Сообщений: ≥200\n"
                norm_text += f"  • Награда: 200 Epoints\n"
            
            elif target_role == 12:
                norm_text += "Норма Куратора техспециалистов:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Принято форм: ≥3\n"
                norm_text += "  • Обнулений/восстановлений: ≥1\n"
                norm_text += f"  • Награда: 150 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Принято форм: ≥1\n"
                norm_text += f"  • Награда: 75 Epoints\n"
            
            elif target_role == 13:
                norm_text += "Норма Заместителя Главного Тех. Специалиста:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Принято форм: ≥5\n"
                norm_text += "  • Обнулений/восстановлений: ≥2\n"
                norm_text += f"  • Награда: 350 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Принято форм: ≥2\n"
                norm_text += "  • Обнулений/восстановлений: ≥1\n"
                norm_text += f"  • Награда: 175 Epoints\n"
            
            elif target_role == 14:
                norm_text += "Норма Главного Тех. Специалиста:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Принято форм: ≥8\n"
                norm_text += "  • Обнулений/восстановлений: ≥3\n"
                norm_text += f"  • Награда: 450 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Принято форм: ≥4\n"
                norm_text += "  • Обнулений/восстановлений: ≥1\n"
                norm_text += f"  • Награда: 225 Epoints\n"
            
            elif target_role == 15:
                norm_text += "Норма Директора бота:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Сообщений: ≥200\n"
                norm_text += f"  • Награда: 500 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Сообщений: ≥100\n"
                norm_text += f"  • Награда: 250 Epoints\n"
            
            elif target_role == 16:
                norm_text += "Норма Контроля качества:\n\n"
                norm_text += "✅ Выполнена:\n"
                norm_text += "  • Сообщений: ≥200\n"
                norm_text += f"  • Награда: 500 Epoints\n\n"
                norm_text += "⚠️ Натянута:\n"
                norm_text += "  • Сообщений: ≥100\n"
                norm_text += f"  • Награда: 250 Epoints\n"
            
            elif target_role == 17:
                norm_text += "Норма Разработчика бота:\n\n"
                norm_text += "✅ Выполнена (любое действие):\n"
                norm_text += "  • Сообщений: ≥1\n"
                norm_text += f"  • Награда: 500 Epoints\n"
            
            kb = Keyboard(inline=True)
            kb.add(Callback("🔄 Обновить норму", {"command": "norm_refresh", "user": target_user_id, "owner_id": user_id}))
            
            await message.answer(norm_text, keyboard=kb, disable_mentions=1)
            return True
        
        
        if command in ['astatsreset', 'астатсресет']:
            role = await get_role(user_id, chat_id)
            if not _role_can_satisfy_standard_min(role, 17):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            arg = arguments[1].lower() if len(arguments) >= 2 else ""
            
            if arg == "week":
                await reset_weekly_admin_stats()
                await message.reply("Статистика за прошлую неделю сброшена.", disable_mentions=1)
            elif arg == "all":
                async with DB_WRITE_LOCK:
                    db = await connect_sqlite()
                    try:
                        await db.execute("DELETE FROM admin_stats")
                        await db.execute("DELETE FROM admin_norms")
                        await db.commit()
                    finally:
                        await db.close()
                await message.reply("Полная очистка статистики администраторов выполнена.", disable_mentions=1)
            else:
                await message.reply("Использование: /astatsreset week | /astatsreset all", disable_mentions=1)
            return True


        if command in ['sremovenick', 'srnick']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            server_id = await get_current_server(chat_id)
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            for i in server_chats:
                try:
                    await rnick(user, i)
                except:
                    pass

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал(-а) ник в беседах сервера «{server_id}» @id{user} (пользователю)",
                disable_mentions=1
            )
            await chats_log(
                user_id=user_id, target_id=user, role=None,
                log=f"убрал(-а) ник в беседах сервера @id{user} (пользователю)"
            )

        if command in ['ssnick', 'ssetnick', 'ссетник', 'ссник']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            new_nick = await get_string(arguments, arg)
            server_id = await get_current_server(chat_id)
            if not new_nick:
                await message.reply("Укажите ник пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id, message) == 0:
                await message.reply("Вы не можете установить ник данному пользователю!", disable_mentions=1)
                return True

            for i in server_chats:
                try:
                    await setnick(user, i, new_nick)
                except:
                    pass

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) установил новое имя в беседах сервера «{server_id}» @id{user} (пользователю)!\nНовый ник: {new_nick}",
                disable_mentions=1
            )
            await chats_log(
                user_id=user_id, target_id=user, role=None,
                log=f"установил(-а) новый ник в беседах сетки @id{user} (пользователю). Новый ник: {new_nick}"
            )

        if command in ['skick', 'снят', 'скик']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            server_id = await get_current_server(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)

            kicked_chats = 0
            failed_chats = 0
            for i in server_chats:
                try:
                    await bot.api.messages.remove_chat_user(i, user)
                    kicked_chats += 1
                    msg = f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) в беседах сервера «{server_id}» @id{user} ({await get_user_name(user, chat_id)})"
                    if reason:
                        msg += f"\nПричина: {reason}"
                    await bot.api.messages.send(peer_id=2000000000 + i, message=msg, disable_mentions=1, random_id=0)
                except Exception:
                    failed_chats += 1

            if kicked_chats <= 0:
                await message.answer(
                    f"Не удалось исключить @id{user} (пользователя) ни из одной беседы сервера «{server_id}».",
                    disable_mentions=1,
                )
                return True

            fail_note = f"\nНе удалось исключить в {failed_chats} бесед(е/ах)." if failed_chats > 0 else ""
            result_text = (
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) "
                f"@id{user} ({await get_user_name(user, chat_id)}) из {kicked_chats} бесед сервера «{server_id}»."
            )
            if reason:
                result_text += f"\nПричина: {reason}"
            result_text += fail_note
            await message.answer(result_text, disable_mentions=1)

            await chats_log(user_id=user_id, target_id=user, role=None,
                            log=f"исключил(-а) @id{user} (пользователя) в сетке бесед")
            await add_punishment(chat_id, user_id)

        if command in ['sremoverole', 'srrole']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            server_id = await get_current_server(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете снять роль данному пользователю!", disable_mentions=1)
                return True

            for i in server_chats:
                try:
                    await roleG(user, i, 0)
                except:
                    pass

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) роль в беседах сервера «{server_id}» у @id{user} (пользователя)",
                disable_mentions=1
            )
            await chats_log(
                user_id=user_id, target_id=user, role=None,
                log=f"забрал(-а) роль в беседах сервера @id{user} (пользователя)"
            )

        if command in ['sban', 'сбан']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            server_id = await get_current_server(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.reply("Вы не можете заблокировать данного пользователя!", disable_mentions=1)
                return True

            reason = get_raw_text_tail(raw_text, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            for i in server_chats:
                try:
                    await ban(user, user_id, i, reason)
                    await bot.api.messages.remove_chat_user(i, user)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Снять бан", {"command": "unban", "user": user, "chatId": chat_id}),
                             color=KeyboardButtonColor.POSITIVE)
                    )
                    await bot.api.messages.send(peer_id=2000000000 + i,
                                                message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) в беседах сервера «{server_id}» @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}",
                                                disable_mentions=1, random_id=0, keyboard=keyboard)
                except:
                    pass

            await chats_log(user_id=user_id, target_id=user, role=None,
                            log=f"заблокировал(-а) @id{user} (пользователя) в беседах сервера")
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) в беседах сервера «{server_id}» @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}", disable_mentions=1)                
            await add_punishment(chat_id, user_id)

        if command in ['sunban', 'санбан', 'сунбан']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            
            server_chats = await get_server_chats(chat_id)
            server_id = await get_current_server(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            for i in server_chats:
                try:
                    await unban(user, i)
                except:
                    pass

            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) в беседах сервера «{server_id}» @id{user} ({await get_user_name(user, chat_id)})")
            await chats_log(user_id=user_id, target_id=user, role=None,
                            log=f"разблокировал(-а) в беседах сервера @id{user} (пользователя)")            


        if command in ['inactivelist', 'inactive', 'ilist']:
            if not await _can_use_command(user_id, chat_id, 2):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,fields=["online_info", "online", "last_seen"])
            users = json.loads(users.model_dump_json())
            unactive_users_day = []
            count_uad = 0
            unactive_users_moon = []
            count_uam = 0
            for i in users["profiles"]:
                try:
                    currency_time = time.time()
                    time_seen = i['last_seen']['time']
                    last_seen_device_list = {1: "📱", 2: "📱", 3: "📱", 4: "📱", 5: "📱", 6: "💻", 7: "💻"}
                    last_seen_device = last_seen_device_list.get(i['last_seen']['platform'])
                    if time_seen <= currency_time - 604800:
                        count_uam = count_uam + 1
                        if count_uam <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_moon.append(
                                f"{count_uam}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                    elif time_seen <= currency_time - 86400:
                        count_uad = count_uad + 1
                        if count_uad <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_day.append(
                                f"{count_uad}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                except:
                    pass
            uad = "\n".join(unactive_users_day)
            uam = "\n".join(unactive_users_moon)
            await message.replyLocalizedMessage('command_inactivelist', {
                        'day': uad,
                        'week': uam
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"посмотрел(-а) список неактивных пользователей в чате")            

        if command in ['mkick', 'мкик', 'masskick']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) <= 1:
                arguments = 'all'
                return True
            if len(arguments) >= 30:
                arguments = 'all'
                return True

            if arguments[1] in ['all', 'все']:
                if not await _can_use_command(user_id, chat_id, 7):
                    await message.replyLocalizedMessage('not_preminisionss')
                    return True

                users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,
                                                                        fields=["online_info", "online"])
                users = json.loads(users.model_dump_json())
                user_f = []
                gi = 0
                for i in users["profiles"]:
                    if not i['id'] == user_id and not await _can_use_command(i['id'], chat_id, 1):
                        await bot.api.messages.remove_chat_user(chat_id, int(i['id']))

                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) пользователей без ролей", disable_mentions=1)
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"исключил(-а) пользователей без ролей в чате")            
                return True


            do_users = []
            for i in range(len(arguments)):
                if i <= 0:
                    pass
                else:
                    do_users.append(arguments[i])
            users = []
            for i in do_users:
                idp = await getID(i)
                if idp:
                    users.append(idp)
            kick_users_list = []
            for i in users:
                if await equals_roles(user_id, i, chat_id) < 2:
                    await message.answer(f"У @id{i} уровень прав выше!", disable_mentions=1)
                else:
                    try:
                        await bot.api.messages.remove_chat_user(chat_id, i)
                        info = await bot.api.users.get(int(i))
                        kick_users_list.append(f"@id{i} ({info[0].first_name})")
                    except:
                        pass
            kick_users = ", ".join(kick_users_list)
            await message.replyLocalizedMessage('command_masskick')
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['quiet', 'silence', 'тишина']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            silence = await quiet(chat_id)
            if silence:
            	await message.replyLocalizedMessage('command_quiet_on', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })
            	
            else:
            	await message.replyLocalizedMessage('command_quiet_off', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })

        if command in ['addsenmoder', 'senmoder', 'смодер']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 2):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addsenmoder', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права старшего модератора @id{user} (пользователю)")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['rnickall', 'allrnick', 'arnick', 'mrnick']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            await rnickall(chat_id)
            await message.replyLocalizedMessage('command_rnickall', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"очистил(-а) ники в беседе!")            

        if command in ['addadmin', 'admin', 'админ']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 3):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addadmin', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права администратора @id{user} (пользователю)")            
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 5):
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['demote']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.model_dump_json())
            for i in users["profiles"]:
                if not i['id'] == user_id and not await _can_use_command(i['id'], chat_id, 1):
                    try: await bot.api.messages.remove_chat_user(chat_id, i['id'])
                    except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) всех участников без ролей!", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"исключил(-а) пользователей без ролей в чате")            

        if command in ['filter']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if await get_filter(chat_id):
                await set_filter(chat_id, 0)
                await message.replyLocalizedMessage('command_filter_off', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"включил(-а) фильтр в чате")            
            else:
                await set_filter(chat_id, 1)
                await message.replyLocalizedMessage('command_filter_on', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"выключил(-а) фильтр в чате")            

        if command in ['antiflood', 'af']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if await get_antiflood(chat_id):
                await set_antiflood(chat_id, 0)
                await message.replyLocalizedMessage('command_antiflood_off', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"включил(-а) антифлуд в чате")            
            else:
                await set_antiflood(chat_id, 1)
                await message.replyLocalizedMessage('command_antiflood_on', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
                await chats_log(user_id=user_id, target_id=None, role=None, log=f"выключил(-а) антифлуд в чате")            

        if command in ['welcome', 'welcometext', 'wtext']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2:
                await message.replyLocalizedMessage('command_welcometext_params', {
                        'wtext': await get_welcome(chat_id)
                    })            
                return True

            text = get_raw_text_tail(raw_text, 1)
            await set_welcome(chat_id, text)
            await message.replyLocalizedMessage('command_welcometext', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=None, role=None, log=f"установил(-а) новое приветствие в чате. Новое привтетствие: {text}")            

        if command in ['invite']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            result = await invite_kick(chat_id, True)
            if result: await message.replyLocalizedMessage('command_invite_on') is await chats_log(user_id=user_id, target_id=None, role=None, log=f"включил(-а) функцию приглашения модераторами в чате")                        
            else: await message.replyLocalizedMessage('command_invite_off') is await chats_log(user_id=user_id, target_id=None, role=None, log=f"выключил(-а) функцию приглашения модераторами в чате")                        

        if command in ['leave']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            result = await leave_kick(chat_id, True)
            if result: await message.replyLocalizedMessage('command_leave_on') is await chats_log(user_id=user_id, target_id=None, role=None, log=f"включил(-а) функцию исключения при выходе")                        
            else: await message.replyLocalizedMessage('command_leave_off') is await chats_log(user_id=user_id, target_id=None, role=None, log=f"выключил(-а) функцию исключения при выходе")                        

        if command in ['addsenadmin', 'addsenadm', 'senadm', 'senadmin', 'садмин']:
            if not await _can_use_command(user_id, chat_id, 5):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 4):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addsenadmin', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права старшего администратора @id{user} (пользователю)")            
            
        if command in ['addzsa', 'зса']:
            if not await _can_use_command(user_id, chat_id, 6):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 5):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addzsa', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права зам спец администратора @id{user} (пользователю)")            
            
        if command in ['addsa', 'са', 'spec', 'specadm']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            if not await _grant_standard_role_to_user(user, chat_id, 6):
                await message.reply("В модифицированной беседе нет роли для этого уровня.", disable_mentions=1)
                return True
            await message.replyLocalizedMessage('command_addsa', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права спец администратора @id{user} (пользователю)")            

        if command in ['serverinfo', 'серверинфо']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            server_number = await get_current_server(chat_id)
            server_chats = await get_server_chats(chat_id)
            if not server_number or not server_chats:
                await message.reply("Для начала укажите сервер, /server!", disable_mentions=1)
                return True

            chats_list = ""
            for i, linked_chat_id in enumerate(server_chats, start=1):
                chat_title, _chat_link = await get_chat_display_info(int(linked_chat_id))
                chats_list += f"{i}. {chat_title}\n"

            await message.replyLocalizedMessage('command_serverinfo', {
                        'server': server_number,
                        'count_chats': len(server_chats),
                        'info': chats_list
                    })            
            return True
            
        if command in ['server', 'сервер']:
            if not await _can_use_command(user_id, chat_id, 6):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if len(arguments) < 2:
                await message.replyLocalizedMessage('command_server_params')
                return True

            server_number = arguments[1]
            server = arguments[1]

            if not server_number.isdigit():
                await message.replyLocalizedMessage('command_server_number')
                return True

            chat_owner_id = await resolve_chat_owner_id(chat_id, message.peer_id)
            server_owner_id = int(chat_owner_id) if int(chat_owner_id or 0) > 0 else int(user_id)
            table_name = f"server_{server_owner_id}_{server_number}"

            
            if server_number == "0":
                unlink_chat_from_all_server_tables(chat_id)
                await message.replyLocalizedMessage('command_server_un')
                return True

            
            sql.execute("SELECT * FROM servers_list WHERE owner_id = ? AND server_number = ?", (server_owner_id, server_number))
            exists_server = sql.fetchone()

            sql.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                chat_id INTEGER,
                chat_title TEXT
            )
            """)
            if not exists_server:
                
                sql.execute("INSERT INTO servers_list (owner_id, server_number, table_name) VALUES (?, ?, ?)",
                            (server_owner_id, server_number, table_name))
                database.commit()

            unlink_chat_from_all_server_tables(chat_id, keep_table_name=table_name)
            
            sql.execute(f"SELECT chat_id FROM {table_name} WHERE chat_id = ?", (chat_id,))
            if sql.fetchone():            	
                sql.execute("DELETE FROM server_links WHERE chat_id = ?", (chat_id,))
                sql.execute(
                    "INSERT INTO server_links (server_id, chat_id, chat_title, owner_id, server_number) VALUES (?, ?, COALESCE((SELECT chat_title FROM chats WHERE chat_id = ?), ?), ?, ?)",
                    (int(server_number), chat_id, chat_id, "Без названия", server_owner_id, server_number),
                )
                database.commit()
                await message.replyLocalizedMessage('command_server_alyready', {
                        'server': server
                    })            
                return True

            
            try:
                chat_info = await bot.api.messages.get_conversations_by_id(peer_ids=message.peer_id)
                chat_title = chat_info.items[0].chat_settings.title if chat_info.items else "Без названия"
            except:
                chat_title = "Без названия"

            sql.execute(f"INSERT INTO {table_name} (chat_id, chat_title) VALUES (?, ?)", (chat_id, chat_title))
            sql.execute(
                "INSERT INTO server_links (server_id, chat_id, chat_title, owner_id, server_number) VALUES (?, ?, ?, ?, ?)",
                (int(server_number), chat_id, chat_title, server_owner_id, server_number),
            )
            database.commit()

            await message.replyLocalizedMessage('command_server', {
                        'server': server
                    })            
            return True            
            
        if command in ['setowner', 'владелец', 'владелецбеседы']:
            if not await _has_role_level(user_id, chat_id, 11):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2:
                raw_target = str(arguments[1]).strip()
                club_match = re.search(r"(?:club|public)(\d+)", raw_target, re.IGNORECASE)
                if club_match:
                    user = -int(club_match.group(1))
                else:
                    resolved_id = await getID(raw_target)
                    if resolved_id:
                        user = int(resolved_id)
                    else:
                        user = int
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if user == int:
                await message.replyLocalizedMessage('select_user')
                return True

            if user > 0 and await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await set_onwer(user, chat_id)
            if user > 0:
                await roleG(user, chat_id, 0)
                target_label = format_vk_link(user, await get_user_name(user, chat_id))
                target_log = format_vk_link(user, "пользователю")
            else:
                target_label = format_vk_link(user, "сообществу")
                target_log = format_vk_link(user, "сообществу")
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права владельца беседы {target_label}", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права владельца беседы {target_log}")
            return True
                        
        if command in ['addblogger', 'blogger', 'аддблогер', 'блогер']:
            
            
            if not await _has_role_level(user_id, chat_id, 14):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 1)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права Медиа Партнера @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права Медиа Партнера @id{user} (пользователю)")
            return True

        if command in ['addtechspec', 'techspec', 'аддтехспец', 'техспец', 'тех']:
            if not await _has_role_level(user_id, chat_id, 12):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 8)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права технического специалиста @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права технического специалиста @id{user} (пользователю)")
            return True

        if command in ['addtechcurator', 'techcurator', 'аддкуратортех', 'куратортех', 'ктех']:
            if not await can_use_admin_grant_commands(user_id, chat_id, 14):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, GLOBAL_LEVEL_TECH_CURATOR)
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права {GLOBAL_TECH_CURATOR_NAME} "
                f"@id{user} ({await get_user_name(user, chat_id)})",
                disable_mentions=1,
            )
            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"выдал(-а) права {GLOBAL_TECH_CURATOR_NAME} @id{user} (пользователю)",
            )
            return True

        if command in ['addgtech', 'глтех', 'главныйтех', 'гтех']:
            if not await can_use_admin_grant_commands(user_id, chat_id, 16):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 5)
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права Главного Тех. Специалиста "
                f"@id{user} ({await get_user_name(user, chat_id)})",
                disable_mentions=1,
            )
            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"выдал(-а) права Главного Тех. Специалиста @id{user} (пользователю)",
            )
            return True

        if command in ['addzgtech', 'замглавтех', 'замглтех']:
            if not await can_use_admin_grant_commands(user_id, chat_id, 16):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 3)
            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права Зам. Главного Тех. Специалиста "
                f"@id{user} ({await get_user_name(user, chat_id)})",
                disable_mentions=1,
            )
            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"выдал(-а) права Зам. Главного Тех. Специалиста @id{user} (пользователю)",
            )
            return True

        if command in ['addzamdirector', 'addzamd', 'аддзам', 'заместитель']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 2)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права заместитель директора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права заместитель директора @id{user} (пользователю)")            

        if command in ['addgltester', 'gltester', 'аддглтестер', 'главныйтестер']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.reply("Вы не являетесь тестировщиком бота!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 5)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права главного тестировщика @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права главного тестировщика @id{user} (пользователю)")                        
            
        if command in ['addzamtester', 'addzamt', 'аддзамтестер', 'заместительтестера']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.reply("Вы не являетесь тестировщиком бота!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 3)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права заместителя главного тестировщика @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права заместителя тестировщика @id{user} (пользователю)")                        
            
        if command in ['addoszamdirector', 'addoszamd', 'аддосзам', 'озаместитель']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 4)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права основного заместителя директора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права основного заместителя директора @id{user} (пользователю)")            
            
        if command in ['addkk', 'kk', 'аддкк']:
            if not await _has_role_level(user_id, chat_id, 17):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, GLOBAL_LEVEL_QUALITY_CONTROL)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) роль Контроля качества @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) роль Контроля качества @id{user} (пользователю)")
            return True

        if command in ['adddirector', 'director', 'адддиректор', 'директор']:
            if not await _has_role_level(user_id, chat_id, 16):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 6)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права Директора бота @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права Директора бота @id{user} (пользователю)")
            return True

        if command in ['adddev', 'developer', 'аддразработчик', 'разработчик']:
            allowed_ids = [488828183,574393629]  

            if user_id not in allowed_ids:
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            
            if user in allowed_ids and user_id in allowed_ids:
                await message.reply("Вы не можете выдать роль другому grrole пользователю!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id, message) < 2:
                await message.replyLocalizedMessage('set_role_preminisionss')
                return True

            await globalrole(user, 7)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права разработчика бота @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"выдал(-а) права разработчика бота @id{user} (пользователю)")    

        if command in ['sayall', 'gzov', 'news']:
            if not await _has_role_level(user_id, chat_id, 15):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            reason = get_raw_text_tail(raw_text, 1)
            if not reason:
                await message.reply("Укажите текст рассылки!")
                return True

            peer_ids = await (get_gzov_peerids() if command == 'gzov' else get_all_peerids())
            for i in peer_ids:
                try:
                    target_chat_id = int(i) - 2000000000 if command == 'gzov' and int(i) >= 2000000000 else 0
                    keyboard = build_gzov_disable_keyboard(target_chat_id) if command == 'gzov' and target_chat_id > 0 else None
                    await bot.api.messages.send(peer_id=i, message=reason, disable_mentions=1, keyboard=keyboard, random_id=0)
                except: pass
            await message.reply("Рассылка отправлена.", disable_mentions=1)
            return True

        if command in ['gzovoff']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if await get_gzov_disabled(chat_id):
                await message.reply("Глобальная рассылка уже отключена в этом чате.", disable_mentions=1)
                return True

            await set_gzov_disabled(chat_id, 1)
            await message.reply("Глобальная рассылка отключена в этом чате.", disable_mentions=1)
            return True

        if command in ['gzovon']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if not await get_gzov_disabled(chat_id):
                await message.reply("Глобальная рассылка уже включена в этом чате.", disable_mentions=1)
                return True

            await set_gzov_disabled(chat_id, 0)
            await message.reply("Глобальная рассылка включена в этом чате.", disable_mentions=1)
            return True
                
        if command in ['deltester', 'untester', 'снятьтестера']:
            if not await _has_role_level(user_id, chat_id, 17):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            user = await get_string(arguments, 1)
            if not user:
                await message.reply("Укажите пользователя!")
                return True

            peer_ids = await get_all_peerids()
            await roleG(user, peer_ids, 0)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), снял(-а) роль тестеровщика во всех беседах у @id{user} ({await get_user_name(user, chat_id)})")               

        if command in ['szov', 'serverzov', 'сзов']:
            if not await _can_use_command(user_id, chat_id, 4):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            reason = get_raw_text_tail(raw_text, 1)
            if not reason:
                await message.reply("Укажите причину вызова!", disable_mentions=1)
                return True

            
            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            
            for i in server_chats:
                try:
                    users = await bot.api.messages.get_conversation_members(peer_id=2000000000 + i, fields=["online_info", "online"])
                    users = json.loads(users.model_dump_json())
                    user_f = []
                    gi = 0
                    for b in users["profiles"]:
                        if not b['id'] == user_id:
                            gi += 1
                            if gi <= 100:
                                user_f.append(f"@id{b['id']} (🖤)")
                    zov_users = ''.join(user_f)

                    await bot.api.messages.send(
                        peer_id=2000000000 + i,
                        message=(
                            f"🔔 Вы были вызваны @id{user_id} (администратором) бесед\n\n"
                            f"{zov_users}\n\n"
                            f"❗ Причина вызова: {reason}"
                        ),
                        random_id=0
                    )
                except Exception as e:
                    print(f"[SZOV] Ошибка при отправке вызова в беседу {i}: {e}")

            await chats_log(user_id=user_id, target_id=None, role=None, log=f"вызвал(-а) всех пользователей в беседах сервера. Причина: {reason}")
            await message.reply(f"📣 Вызов успешно отправлен во все беседы сервера!\nПричина: {reason}", disable_mentions=1)
            return True
            
        if command in ['editowner', 'owner']:
            if not await _can_use_command(user_id, chat_id, 7):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.replyLocalizedMessage('testers_chat')
                return True

            user = int
            if len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if user == user_id: return await message.replyLocalizedMessage('command_editowner_user_user')

            if len(arguments) <= 2: return await message.replyLocalizedMessage('command_editowner_confirm')
            if not arguments_lower[2] == "confirm":
                return await message.replyLocalizedMessage('command_editowner_confirm')

            await set_onwer(user, chat_id)
            if not await _grant_standard_role_to_user(user_id, chat_id, 6):
                await roleG(user_id, chat_id, 0)

            await message.replyLocalizedMessage('command_editowner', {
                        'user': f'@id{user_id} ({await get_user_name(user_id, chat_id)})',
                        'target': f'@id{user} ({await get_user_name(user, chat_id)})'
                    })            
            await chats_log(user_id=user_id, target_id=user, role=None, log=f"передал(-а) права владельца беседы @id{user} (пользователю)")            

        if command in ['srole', 'сроле']:
            if not await _can_use_command(user_id, chat_id, 3):
                await message.replyLocalizedMessage('not_preminisionss')
                return True

            if chat_id == tchat:
                await message.reply(
                    "Данная беседа проводится в специализированном чате, который предназначен исключительно для тестировщиков бота.\n\n"
                    "В рамках данного обсуждения не допускается использование команд, не относящихся к работе по тестированию или функционированию системы в целом.",
                    disable_mentions=1
                )
                return True

            user = int
            arg = 2
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.replyLocalizedMessage('select_user')
                return True

            if await equals_roles(user_id, user, chat_id, None) < 2:
                return await message.reply("Вы не можете взаимодействовать с данным пользователем!")

            if len(arguments) < arg + 1:
                return await message.reply("Укажите аргументы!")

            if not arguments[arg].isdigit():
                return await message.reply("Укажите число!")

            level_num = int(arguments[arg])
            my_role = await get_role(user_id, chat_id)
            if not _is_encoded_global_role(my_role) and level_num >= _effective_standard_role_level(my_role):
                return await message.reply("Вы не можете выдать роль, которая выше вашей!")

            if level_num < 1 or level_num > 6:
                return await message.reply("Для /srole доступен только уровень от 1 до 6.")

            
            roles_dict = {
                1: "модератора",
                2: "старшего модератора",
                3: "администратора",
                4: "старшего администратора",
                5: "зам. спец администратора",
                6: "спец. администратора"
            }
            level_name = roles_dict.get(level_num, f"уровень {level_num}")
            server_id = await get_current_server(chat_id)
            
            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначало укажите сервер, /server!", disable_mentions=1)
                return True

            
            applied_chats = 0
            for i in server_chats:
                try:
                    target_level = await _standard_role_level_for_target_chat(i, level_num)
                    if target_level is None:
                        continue
                    await roleG(user, i, target_level)
                    applied_chats += 1
                except Exception as e:
                    print(f"[SROLE] Ошибка при выдаче роли в беседе {i}: {e}")

            if applied_chats <= 0:
                await message.reply("Не найдено бесед сетки с подходящим форматом этой роли.", disable_mentions=1)
                return True

            await message.reply(
                f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права {level_name} "
                f"в беседах сервера «{server_id}» @id{user} ({await get_user_name(user, chat_id)})"
            )
            await chats_log(
                user_id=user_id,
                target_id=user,
                role=None,
                log=f"выдал(-а) права «{level_name}» в сетке бесед сервера @id{user} (пользователю)"
            )
            return True
            


    else:
        if user_id < 1: return True
        if await check_chat(chat_id):
            if await get_mute(user_id, chat_id) and not await checkMute(chat_id, user_id):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
            elif await check_quit(chat_id) and not await _has_role_level(user_id, chat_id, 1):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                print(not await _can_use_command(user_id, chat_id, 1))
            else:
                if await get_filter(chat_id):
                    bws = await get_active_banwords(chat_id)
                    for i in bws:
                        if i in (message.text or "").lower() and not await _can_use_command(user_id, chat_id, 1):
                            await add_mute(user_id, chat_id, 'Бот', 'Написание запрещенных слов', 30)
                            await add_mutelog(chat_id, user_id, -123456789, "Написание запрещенных слов", 30, "выдан")
                            keyboard = (
                                Keyboard(inline=True)
                                .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                            )
                            await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за написание запрещенного слова!", disable_mentions=1, keyboard=keyboard)
                            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                            except: pass
                            return True

            await new_message(user_id, message.message_id, message.conversation_message_id, chat_id)
            if await get_spam(user_id, chat_id) and not await _can_use_command(user_id, chat_id, 1):
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                )
                await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за спам!", disable_mentions=1, keyboard=keyboard)
                await add_mute(user_id, chat_id, 'Bot', 'Спам', 30)
                try:await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                except: pass

async def start_tasks():
    pass


async def restore_giveaway_tasks():
    for giveaway_id in list(giveaways.keys()):
        asyncio.create_task(finish_giveaway(giveaway_id))

if __name__ == "__main__":
    def _handle_exception(loop, context):
        exception = context.get("exception")
        if exception:
            if isinstance(exception, sqlite3.OperationalError):
                print(f"[EVENT LOOP] SQLite error: {exception}")
                _reset_sql_cursor()
            else:
                print(f"[EVENT LOOP] Error: {exception}")
                _reset_sql_cursor()
        else:
            print(f"[EVENT LOOP] {context.get('message', 'unknown')}")

    async def _configure_runtime_loop():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_handle_exception)

        print("\033[92mБот получен, запуск!\033[0m")
        asyncio.create_task(_finish_pending_restart_progress())

    bot.loop_wrapper.on_startup.extend([
        _configure_runtime_loop(),
    ])
    bot.loop_wrapper.add_task(start_tasks())
    
    
    
    
    

    
    async def _bothost_health():
        try:
            from aiohttp import web
            async def h(r): return web.Response(text="OK")
            a = web.Application()
            a.router.add_get("/", h)
            a.router.add_get("/health", h)
            runner = web.AppRunner(a)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", 3000)
            await site.start()
            print("[BOTHOST] Health server on :3000")
        except Exception as e:
            print(f"[BOTHOST] Health error: {e}")

    bot.loop_wrapper.add_task(_bothost_health())
    bot.run_forever()
