from __future__ import annotations

import asyncio
import json
import os
import random
import database_pg as sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from utils.business import BUSINESSES_CATALOG, PURCHASEABLE_BUSINESS_KEYS, add_business
from utils.case_system import CASE_DEFS, add_user_cases_bulk
from utils.inventory import add_item
from utils.json_store import load as _json_store_load, save as _json_store_save
from utils.pass_system import CURRENT_PASS_SEASON, init_pass_schema
from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite


SHOP_DB_FILE = "database.db"
BALANCES_FILE = "balances.json"
DICE_DEALERS_FILE = "dice_dealers.json"
SHOP_CALLBACK_PREFIX = "shop_"


@dataclass(frozen=True)
class ShopProduct:
    product_id: str
    category: str
    title: str
    price_rub: int
    description: str
    rewards: dict[str, Any] = field(default_factory=dict)
    permanent: bool = False


PRODUCTS: dict[str, ShopProduct] = {
    # Коины
    "bananas_100": ShopProduct("bananas_100", "bananas", "100 коинов", 99, "100 🪙 на игровой баланс.", {"bananas": 100}),
    "bananas_250": ShopProduct("bananas_250", "bananas", "250 коинов", 199, "250 🪙 на игровой баланс.", {"bananas": 250}),
    "bananas_700": ShopProduct("bananas_700", "bananas", "700 коинов", 499, "700 🪙 на игровой баланс.", {"bananas": 700}),
    "bananas_1500": ShopProduct("bananas_1500", "bananas", "1 500 коинов", 899, "1 500 🪙 на игровой баланс.", {"bananas": 1500}),
    "bananas_5000": ShopProduct("bananas_5000", "bananas", "5 000 коинов", 2499, "5 000 🪙 на игровой баланс.", {"bananas": 5000}),

    # VIP
    "vip_30": ShopProduct("vip_30", "vip", "VIP на 30 дней", 199, "VIP-статус на 30 дней.", {"vip_days": 30}),
    "vip_90": ShopProduct("vip_90", "vip", "VIP на 90 дней", 499, "VIP-статус на 90 дней.", {"vip_days": 90}),
    "vip_180": ShopProduct("vip_180", "vip", "VIP на 180 дней", 799, "VIP-статус на 180 дней.", {"vip_days": 180}),
    "vip_365": ShopProduct("vip_365", "vip", "VIP на 365 дней", 1299, "VIP-статус на 365 дней.", {"vip_days": 365}),

    # NOLIM
    "nolim_30": ShopProduct("nolim_30", "nolim", "NOLIM на 30 дней", 999, "Безлимит переводов, усиленная /подписка, 10 случайных кейсов.", {"nolim_days": 30}),
    "nolim_90": ShopProduct("nolim_90", "nolim", "NOLIM на 90 дней", 2499, "Подписка NOLIM на 90 дней.", {"nolim_days": 90}),
    "nolim_365": ShopProduct("nolim_365", "nolim", "NOLIM на 365 дней", 7999, "Подписка NOLIM на 365 дней.", {"nolim_days": 365}),

    # Наборы кейсов
    "pack_starter": ShopProduct(
        "pack_starter", "packs", "🥉 Стартовый набор", 99,
        "10 кейсов Бомжа и 100 коинов.",
        {"bananas": 100, "cases": {"homeless": 10}},
    ),
    "pack_silver": ShopProduct(
        "pack_silver", "packs", "🥈 Серебряный набор", 249,
        "20 Стандартных кейсов и 300 коинов.",
        {"bananas": 300, "cases": {"standard": 20}},
    ),
    "pack_gold": ShopProduct(
        "pack_gold", "packs", "🥇 Золотой набор", 499,
        "30 Особых кейсов, 700 коинов и VIP на 30 дней.",
        {"bananas": 700, "vip_days": 30, "cases": {"special": 30}},
    ),
    "pack_diamond": ShopProduct(
        "pack_diamond", "packs", "💎 Платиновый набор", 999,
        "50 Админ-кейсов, 1 500 коинов и VIP на 90 дней.",
        {"bananas": 1500, "vip_days": 90, "cases": {"admin": 50}},
    ),
    "pack_ultimate": ShopProduct(
        "pack_ultimate", "packs", "👑 Ultimate", 1999,
        "100 Админ-кейсов, 5 000 коинов, NOLIM на 30 дней, Premium PASS и случайный бизнес.",
        {"bananas": 5000, "nolim_days": 30, "cases": {"admin": 100}, "premium_pass": True, "random_business": True},
    ),

    # PASS
    "pass_premium": ShopProduct(
        "pass_premium", "pass", "Premium ADMIN PASS", 399,
        "Premium-доступ текущего сезона ADMIN PASS.", {"premium_pass": True}, permanent=True,
    ),

    # Должности
    "dealer_license": ShopProduct(
        "dealer_license", "jobs", "🎰 Должность «Крупье»", 250,
        "Постоянный доступ к созданию столов /dice и получению комиссии.",
        {"dealer_license": True}, permanent=True,
    ),
}

CATEGORY_TITLES = {
    "bananas": "🪙 Коины",
    "vip": "👑 VIP",
    "nolim": "💠 NOLIM",
    "packs": "🎁 Наборы",
    "pass": "🎟 ADMIN PASS",
    "jobs": "🎰 Должности",
}

_balance_lock = asyncio.Lock()
_shop_db_lock = asyncio.Lock()


def _load_json(path: str, default: Any) -> Any:
    # Данные хранятся во внешней PostgreSQL (таблица json_store).
    data = _json_store_load(path, default=None)
    return default if data is None else data


def _save_json(path: str, data: Any) -> None:
    _json_store_save(path, data)


def _button(label: str, payload: dict[str, Any], color: str = "secondary") -> dict[str, Any]:
    return {"action": {"type": "callback", "label": label, "payload": json.dumps(payload, ensure_ascii=False)}, "color": color}


def _keyboard(rows: list[list[dict[str, Any]]], inline: bool = True) -> str:
    return json.dumps({"inline": inline, "buttons": rows}, ensure_ascii=False)


def main_keyboard() -> str:
    return _keyboard([
        [_button("💠 NOLIM", {"command": "shop_category", "category": "nolim"}, "primary"), _button("👑 VIP", {"command": "shop_category", "category": "vip"}, "primary")],
        [_button("🪙 Коины", {"command": "shop_category", "category": "bananas"}, "positive"), _button("🎁 Наборы", {"command": "shop_category", "category": "packs"}, "positive")],
        [_button("🎟 ADMIN PASS", {"command": "shop_category", "category": "pass"}), _button("🎰 Должности", {"command": "shop_category", "category": "jobs"})],
        [_button("📊 Мои покупки", {"command": "shop_purchases"}), _button("❓ FAQ", {"command": "shop_faq"})],
    ])


def _category_keyboard(category: str) -> str:
    products = [item for item in PRODUCTS.values() if item.category == category]
    rows: list[list[dict[str, Any]]] = []
    for product in products:
        rows.append([_button(f"{product.title} — {product.price_rub}₽", {"command": "shop_product", "product_id": product.product_id}, "primary")])
    rows.append([_button("⬅ Назад", {"command": "shop_home"})])
    return _keyboard(rows)


def _product_keyboard(product: ShopProduct) -> str:
    return _keyboard([
        [_button(f"💳 Купить за {product.price_rub}₽", {"command": "shop_buy", "product_id": product.product_id}, "positive")],
        [_button("⬅ К разделу", {"command": "shop_category", "category": product.category}), _button("🏠 В магазин", {"command": "shop_home"})],
    ])


def build_home_text() -> str:
    return (
        "🛒 Магазин ADMIN MANAGER\n\n"
        "Добро пожаловать в официальный магазин проекта!\n"
        "Здесь доступны подписки, игровая валюта, наборы, Premium PASS и игровые должности.\n\n"
        "👇 Выберите нужный раздел."
    )


def build_category_text(category: str) -> str:
    title = CATEGORY_TITLES.get(category, "Каталог")
    descriptions = {
        "nolim": "♾ Безлимит переводов\n💰 Усиленная награда /подписка\n📦 10 случайных кейсов при получении бонуса",
        "vip": "VIP повышает игровые лимиты и открывает дополнительные преимущества.",
        "bananas": "Коины используются в экономике, событиях и ADMIN PASS.",
        "packs": "Выгодные комплекты кейсов, коинов и подписок.",
        "pass": "Premium-доступ к расширенной ветке наград текущего сезона.",
        "jobs": "Постоянные игровые должности и лицензии.",
    }
    return f"{title}\n\n{descriptions.get(category, '')}\n\nВыберите товар:"


def build_product_text(product: ShopProduct) -> str:
    permanence = "Навсегда" if product.permanent else "Начисляется сразу после подтверждения оплаты"
    return (
        f"{product.title}\n\n"
        f"{product.description}\n\n"
        f"💳 Стоимость: {product.price_rub}₽\n"
        f"📌 Формат: {permanence}"
    )


async def ensure_shop_schema() -> None:
    async with _shop_db_lock:
        async with DB_WRITE_LOCK:
            db = await connect_sqlite()
            try:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shop_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL,
                        product_id TEXT NOT NULL,
                        amount_rub INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        payment_provider TEXT,
                        payment_id TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        paid_at TEXT,
                        fulfilled_at TEXT,
                        error_text TEXT
                    )
                    """
                )
                await db.execute("CREATE INDEX IF NOT EXISTS idx_shop_orders_user ON shop_orders(user_id, id DESC)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_shop_orders_payment ON shop_orders(payment_provider, payment_id)")
                await db.commit()
            finally:
                await db.close()


def _new_public_id(user_id: int) -> str:
    return f"BM-{int(user_id)}-{int(datetime.now().timestamp())}-{random.randint(1000, 9999)}"


async def create_pending_order(user_id: int, product_id: str) -> dict[str, Any]:
    product = PRODUCTS.get(str(product_id))
    if product is None:
        raise ValueError("Товар не найден")
    await ensure_shop_schema()
    public_id = _new_public_id(user_id)
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "INSERT INTO shop_orders (public_id, user_id, product_id, amount_rub, status) VALUES (?, ?, ?, ?, 'pending')",
                (public_id, int(user_id), product.product_id, int(product.price_rub)),
            )
            await db.commit()
        finally:
            await db.close()
    return {"public_id": public_id, "user_id": int(user_id), "product": product, "status": "pending"}


async def _extend_balance_date(balance: dict[str, Any], field_name: str, days: int) -> str:
    now = datetime.now()
    current_raw = balance.get(field_name)
    current = now
    if current_raw:
        try:
            parsed = datetime.fromisoformat(str(current_raw))
            if parsed > now:
                current = parsed
        except (TypeError, ValueError):
            pass
    result = current + timedelta(days=int(days))
    balance[field_name] = result.isoformat()
    return result.strftime("%d.%m.%Y %H:%M")


async def _grant_balance_rewards(user_id: int, rewards: dict[str, Any]) -> list[str]:
    granted: list[str] = []
    async with _balance_lock:
        balances = _load_json(BALANCES_FILE, {})
        uid = str(int(user_id))
        balance = balances.get(uid)
        if not isinstance(balance, dict):
            balance = {"wallet": 0, "bank": 0, "bananas": 0, "vip_until": None, "nolim_until": None}
            balances[uid] = balance
        if int(rewards.get("bananas", 0) or 0) > 0:
            amount = int(rewards["bananas"])
            balance["bananas"] = int(balance.get("bananas", 0) or 0) + amount
            granted.append(f"{amount:,}".replace(",", ".") + " коинов")
        if int(rewards.get("money", 0) or 0) > 0:
            amount = int(rewards["money"])
            balance["wallet"] = int(balance.get("wallet", 0) or 0) + amount
            granted.append(f"{amount:,}".replace(",", ".") + "₽")
        if int(rewards.get("vip_days", 0) or 0) > 0:
            until = await _extend_balance_date(balance, "vip_until", int(rewards["vip_days"]))
            granted.append(f"VIP до {until}")
        if int(rewards.get("nolim_days", 0) or 0) > 0:
            until = await _extend_balance_date(balance, "nolim_until", int(rewards["nolim_days"]))
            granted.append(f"NOLIM до {until}")
        _save_json(BALANCES_FILE, balances)
    return granted


async def _grant_premium_pass(user_id: int) -> str:
    await init_pass_schema()
    season_id = str(CURRENT_PASS_SEASON["id"])
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO banana_pass_users (user_id, season_id, premium_active, xp, updated_at)
                VALUES (?, ?, 1, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, season_id) DO UPDATE SET premium_active = 1, updated_at = CURRENT_TIMESTAMP
                """,
                (int(user_id), season_id),
            )
            await db.commit()
        finally:
            await db.close()
    return "Premium ADMIN PASS"


async def _grant_dealer_license(user_id: int) -> str:
    data = _load_json(DICE_DEALERS_FILE, {"dealers": []})
    values = data.get("dealers", []) if isinstance(data, dict) else []
    dealers = {int(value) for value in values if str(value).lstrip("-").isdigit()}
    dealers.add(int(user_id))
    _save_json(DICE_DEALERS_FILE, {"dealers": sorted(dealers)})
    return "должность «Крупье»"


async def grant_product(user_id: int, product_id: str) -> list[str]:
    """Единая точка выдачи. Вызывается только после подтверждения оплаты."""
    product = PRODUCTS.get(str(product_id))
    if product is None:
        raise ValueError(f"Неизвестный товар: {product_id}")
    rewards = dict(product.rewards)
    granted = await _grant_balance_rewards(int(user_id), rewards)

    for case_type, quantity in dict(rewards.get("cases", {})).items():
        if case_type not in CASE_DEFS:
            raise ValueError(f"В проекте отсутствует тип кейса: {case_type}")
        await add_user_cases_bulk(int(user_id), str(case_type), int(quantity))
        case_name = str(CASE_DEFS.get(case_type, {}).get("name", case_type))
        granted.append(f"{int(quantity)} × «{case_name}»")

    if rewards.get("random_business"):
        available = [key for key in PURCHASEABLE_BUSINESS_KEYS if key in BUSINESSES_CATALOG]
        if not available:
            raise ValueError("Нет доступных бизнесов для выдачи")
        business_key = random.choice(available)
        await add_business(int(user_id), business_key)
        granted.append(f"бизнес «{BUSINESSES_CATALOG[business_key]['name']}»")

    for business_key, quantity in dict(rewards.get("businesses", {})).items():
        for _ in range(int(quantity)):
            await add_business(int(user_id), str(business_key))
        granted.append(f"{int(quantity)} × бизнес «{BUSINESSES_CATALOG.get(business_key, {}).get('name', business_key)}»")

    for item in list(rewards.get("items", [])):
        await add_item(int(user_id), str(item["item_type"]), str(item["item_name"]), int(item.get("item_value", 0)))
        granted.append(str(item["item_name"]))

    if rewards.get("premium_pass"):
        granted.append(await _grant_premium_pass(int(user_id)))
    if rewards.get("dealer_license"):
        granted.append(await _grant_dealer_license(int(user_id)))
    return granted


async def fulfill_order(
    public_id: str,
    payment_provider: str,
    payment_id: str,
) -> tuple[bool, str, Optional[int]]:
    """Подключается к webhook платежной системы. Повторный вызов безопасен."""
    await ensure_shop_schema()
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT id, user_id, product_id, status FROM shop_orders WHERE public_id = ?",
                (str(public_id),),
            )
            row = await cursor.fetchone()
            if not row:
                await db.rollback()
                return False, "Заказ не найден", None
            order_id, user_id, product_id, status = int(row[0]), int(row[1]), str(row[2]), str(row[3])
            if status == "fulfilled":
                await db.rollback()
                return True, "Заказ уже был выдан", user_id
            if status not in {"pending", "paid", "error"}:
                await db.rollback()
                return False, f"Заказ имеет статус {status}", user_id
            await db.execute(
                "UPDATE shop_orders SET status='paid', payment_provider=?, payment_id=?, paid_at=CURRENT_TIMESTAMP, error_text=NULL WHERE id=?",
                (str(payment_provider), str(payment_id), order_id),
            )
            await db.commit()
        finally:
            await db.close()

    try:
        granted = await grant_product(user_id, product_id)
    except Exception as error:
        async with DB_WRITE_LOCK:
            db = await connect_sqlite()
            try:
                await db.execute("UPDATE shop_orders SET status='error', error_text=? WHERE id=?", (str(error), order_id))
                await db.commit()
            finally:
                await db.close()
        return False, f"Оплата принята, но выдача завершилась ошибкой: {error}", user_id

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute("UPDATE shop_orders SET status='fulfilled', fulfilled_at=CURRENT_TIMESTAMP, error_text=NULL WHERE id=?", (order_id,))
            await db.commit()
        finally:
            await db.close()
    return True, "Выдано: " + ", ".join(granted), user_id


async def get_purchases_text(user_id: int) -> str:
    await ensure_shop_schema()
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            cursor = await db.execute(
                "SELECT public_id, product_id, amount_rub, status, created_at FROM shop_orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (int(user_id),),
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()

    balances = _load_json(BALANCES_FILE, {})
    balance = balances.get(str(int(user_id)), {}) if isinstance(balances, dict) else {}
    vip_until = balance.get("vip_until") or "не активен"
    nolim_until = balance.get("nolim_until") or "не активен"
    dealer_data = _load_json(DICE_DEALERS_FILE, {"dealers": []})
    dealer_ids = dealer_data.get("dealers", []) if isinstance(dealer_data, dict) else []
    dealer_status = "✅ Приобретено" if int(user_id) in {int(x) for x in dealer_ids if str(x).lstrip('-').isdigit()} else "❌ Не приобретено"

    lines = [
        "📊 Мои покупки",
        "",
        f"👑 VIP: {vip_until}",
        f"💠 NOLIM: {nolim_until}",
        f"🎰 Крупье: {dealer_status}",
        "",
        "Последние заказы:",
    ]
    if not rows:
        lines.append("Заказов пока нет.")
    else:
        status_names = {"pending": "ожидает оплаты", "paid": "оплачен", "fulfilled": "выдан", "error": "ошибка выдачи", "cancelled": "отменён"}
        for public_id, product_id, amount_rub, status, created_at in rows:
            title = PRODUCTS.get(str(product_id), ShopProduct(str(product_id), "", str(product_id), int(amount_rub), "")).title
            lines.append(f"• {title} — {int(amount_rub)}₽ | {status_names.get(str(status), status)}\n  {public_id} | {created_at}")
    return "\n".join(lines)


def build_faq_text() -> str:
    return (
        "❓ FAQ магазина\n\n"
        "• Сейчас каталог и система заказов уже работают, но платёжный шлюз ещё не подключён.\n"
        "• После подключения оплаты товар будет выдаваться автоматически.\n"
        "• Каждый заказ имеет уникальный номер и сохраняется в базе.\n"
        "• Повторное уведомление от платёжной системы не выдаст товар второй раз.\n"
        "• При проблеме с покупкой сохраните номер заказа и обратитесь к администрации."
    )


async def handle_private_message(message: Any) -> bool:
    raw_text = str(getattr(message, "text", "") or "").strip()
    command = raw_text.lower().split(maxsplit=1)[0].lstrip("/!+") if raw_text else ""
    if command not in {"магазин", "shop"}:
        return False
    await ensure_shop_schema()
    await message.answer(build_home_text(), keyboard=main_keyboard(), disable_mentions=1)
    return True


async def _edit_callback(bot: Any, event: Any, text: str, keyboard: Optional[str]) -> None:
    await bot.api.messages.edit(
        peer_id=int(event.object.peer_id),
        conversation_message_id=int(event.object.conversation_message_id),
        message=text,
        keyboard=keyboard,
        disable_mentions=1,
    )


async def handle_callback(bot: Any, event: Any, answer_callback: Callable[[Any, str], Awaitable[Any]]) -> bool:
    payload = event.object.payload or {}
    command = str(payload.get("command", "")).lower()
    if not command.startswith(SHOP_CALLBACK_PREFIX):
        return False
    user_id = int(event.object.user_id)
    peer_id = int(event.object.peer_id)
    if peer_id >= 2_000_000_000:
        await answer_callback(event, "Магазин доступен только в личных сообщениях")
        return True

    try:
        if command == "shop_home":
            await _edit_callback(bot, event, build_home_text(), main_keyboard())
            await answer_callback(event, "Главное меню")
            return True
        if command == "shop_category":
            category = str(payload.get("category", ""))
            if category not in CATEGORY_TITLES:
                await answer_callback(event, "Раздел не найден")
                return True
            await _edit_callback(bot, event, build_category_text(category), _category_keyboard(category))
            await answer_callback(event, CATEGORY_TITLES[category])
            return True
        if command == "shop_product":
            product = PRODUCTS.get(str(payload.get("product_id", "")))
            if product is None:
                await answer_callback(event, "Товар не найден")
                return True
            await _edit_callback(bot, event, build_product_text(product), _product_keyboard(product))
            await answer_callback(event, product.title)
            return True
        if command == "shop_buy":
            product = PRODUCTS.get(str(payload.get("product_id", "")))
            if product is None:
                await answer_callback(event, "Товар не найден")
                return True
            if product.product_id == "dealer_license":
                dealer_data = _load_json(DICE_DEALERS_FILE, {"dealers": []})
                current = {int(x) for x in dealer_data.get("dealers", []) if str(x).lstrip('-').isdigit()} if isinstance(dealer_data, dict) else set()
                if user_id in current:
                    await answer_callback(event, "Должность Крупье уже приобретена")
                    return True
            order = await create_pending_order(user_id, product.product_id)
            text = (
                "🧾 Заказ создан\n\n"
                f"Товар: {product.title}\n"
                f"Сумма: {product.price_rub}₽\n"
                f"Номер заказа: {order['public_id']}\n\n"
                "🚧 Платёжная система пока не подключена. Заказ сохранён, но оплачивать его пока не нужно.\n"
                "После подключения платёжного шлюза здесь появится кнопка безопасной оплаты."
            )
            await _edit_callback(bot, event, text, _keyboard([[_button("📊 Мои покупки", {"command": "shop_purchases"})], [_button("🏠 В магазин", {"command": "shop_home"})]]))
            await answer_callback(event, "Заказ создан")
            return True
        if command == "shop_purchases":
            text = await get_purchases_text(user_id)
            await _edit_callback(bot, event, text, _keyboard([[_button("🏠 В магазин", {"command": "shop_home"})]]))
            await answer_callback(event, "История покупок")
            return True
        if command == "shop_faq":
            await _edit_callback(bot, event, build_faq_text(), _keyboard([[_button("🏠 В магазин", {"command": "shop_home"})]]))
            await answer_callback(event, "FAQ")
            return True
    except Exception as error:
        print(f"[SHOP CALLBACK ERROR] user_id={user_id} command={command}: {error}")
        await answer_callback(event, "Ошибка магазина. Попробуйте позже")
        return True
    return False

async def fulfill_exenta_payment(payment_uuid: str, amount: int) -> tuple[bool, str | None, int | None]:
    """Stub for exenta payment fulfillment.
    Returns: (success, detail, user_id)
    """
    # Placeholder implementation - in production this would call the exenta API
async def get_order_by_payment_uuid(payment_uuid: str) -> dict | None:
    """Stub for getting order by payment UUID from exenta.
    Returns: order dict or None
    """
    # Placeholder implementation
    return None
    return True, None, None