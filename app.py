import asyncio
import hashlib
import hmac
import json
import os
import database_pg as sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional
from datetime import date

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for
from utils.runtime_encoding import configure_utf8_stdio
from utils.shop import fulfill_exenta_payment, get_order_by_payment_uuid


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database.db"
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "banana-manager-web-secret")
ENABLE_WEB_LOGS = os.environ.get("ENABLE_WEB_LOGS", "0").strip().lower() in {"1", "true", "yes", "on"}
VK_API_VERSION = "5.199"

configure_utf8_stdio()

config_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
VK_APP_ID = str(config_data.get("application_id", "") or "").strip()
VK_BOT_TOKEN = str(config_data.get("bot-token", "") or "").strip()

TECH_ROLE_NAMES = {
    1: "Младший технический специалист",
    2: "Технический специалист",
    3: "Старший технический специалист",
    4: "Куратор технических специалистов",
    5: "Заместитель главного технического специалиста",
    6: "Главный технический специалист",
}

GLOBAL_ROLE_NAMES = {
    0: "Пользователь",
    1: "Модератор",
    2: "Старший модератор",
    3: "Администратор",
    4: "Старший администратор",
    5: "Зам. спец администратора",
    6: "Спец администратор",
    7: "Владелец беседы",
    8: "Заместитель директора",
    9: "Осн. заместитель директора",
    10: "Директор бота",
    11: "Разработчик бота",
}

LOG_TABLES = {
    "economy": {
        "title": "Логи экономики",
        "description": "Экономические действия с пользователями, суммами и датой операций.",
        "value_column": "amount",
        "value_label": "Сумма",
    },
    "logchats": {
        "title": "Логи бота",
        "description": "Модерация, роли, настройки и служебные действия бота.",
        "value_column": "role",
        "value_label": "Роль поля",
    },
    "exchange_logs": {
        "title": "Логи обменов",
        "description": "Все действия по обменам между пользователями: создание, подтверждение, отмена и завершение.",
        "value_column": "status",
        "value_label": "Статус",
    },
    "banana_market": {
        "title": "Banana Market Logs",
        "description": "Filtered banana buy and sell entries from economy logs.",
        "value_column": "amount",
        "value_label": "Amount",
    },
}

REQUEST_TYPES = [
    ("reset_money", "Обнуление баланса"),
    ("reset_bananas", "Обнуление коинов"),
    ("reset_business", "Обнуление бизнесов"),
    ("reset_vip", "Обнуление VIP"),
    ("reset_pass", "Обнуление ADMIN PASS"),
    ("refund_money", "Возврат монет"),
    ("refund_item", "Возврат предмета"),
    ("refund_business", "Возврат бизнеса"),
]

app = Flask(__name__)
app.secret_key = SESSION_SECRET

_vk_name_cache: dict[int, dict] = {}


@app.before_request
def _block_disabled_web_logs():
    public_paths = {
        "/health",
        "/webhooks/exenta",
        "/payment/success",
        "/payment/failed",
    }
    if ENABLE_WEB_LOGS or request.path in public_paths:
        return None
    return "Web logs are temporarily disabled. Set ENABLE_WEB_LOGS=1 to enable them.", 503


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row[1]) == column_name for row in rows)


def ensure_web_schema() -> None:
    with get_db_connection() as conn:
        for table_name in ("economy", "logchats", "exchange_logs"):
            if _table_exists(conn, table_name) and not _column_exists(conn, table_name, "created_at"):
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN created_at TEXT")
                conn.execute(
                    f"UPDATE {table_name} SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''"
                )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                amount INTEGER,
                details TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                reviewer_id INTEGER,
                resolution_note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_web_requests_status ON web_requests(status, created_at)")
        conn.commit()


def _safe_int(value: Optional[str], default: int, minimum: int = 1, maximum: int = MAX_LIMIT) -> int:
    try:
        parsed = int(str(value or default))
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _safe_user_id(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = str(value).strip()
    return int(value) if value.isdigit() else None


def _get_global_level(conn: sqlite3.Connection, user_id: int) -> int:
    if not _table_exists(conn, "permissions_11"):
        return 0
    row = conn.execute(
        "SELECT level FROM permissions_11 WHERE user_id = ? ORDER BY level DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _get_tech_level(conn: sqlite3.Connection, user_id: int) -> int:
    if not _table_exists(conn, "tech_permissions"):
        return 0
    row = conn.execute(
        "SELECT level FROM tech_permissions WHERE user_id = ? ORDER BY level DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _vk_api_call(method: str, params: dict) -> dict:
    if not VK_BOT_TOKEN:
        return {}
    query = urllib.parse.urlencode({**params, "access_token": VK_BOT_TOKEN, "v": VK_API_VERSION})
    url = f"https://api.vk.com/method/{method}?{query}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload


def _fetch_vk_name_by_id(user_id: int) -> dict:
    if user_id in _vk_name_cache:
        return _vk_name_cache[user_id]

    data = {
        "full_name": f"Пользователь {user_id}",
        "first_name": "Пользователь",
        "last_name": str(user_id),
    }
    try:
        payload = _vk_api_call("users.get", {"user_ids": user_id})
        response = payload.get("response") or []
        if response:
            user = response[0]
            first_name = str(user.get("first_name", "")).strip()
            last_name = str(user.get("last_name", "")).strip()
            data = {
                "full_name": f"{first_name} {last_name}".strip() or f"Пользователь {user_id}",
                "first_name": first_name or "Пользователь",
                "last_name": last_name or str(user_id),
            }
    except Exception:
        pass

    _vk_name_cache[user_id] = data
    return data


def _build_profile(conn: sqlite3.Connection, user_id: Optional[int]) -> dict:
    if not user_id:
        return {
            "id": None,
            "name": "Не указан",
            "role": "—",
            "role_type": "none",
            "vk_url": None,
            "subtitle": "ID: —",
        }

    vk_name = _fetch_vk_name_by_id(user_id)
    global_level = _get_global_level(conn, user_id)
    tech_level = _get_tech_level(conn, user_id)

    if global_level > 0:
        role = GLOBAL_ROLE_NAMES.get(global_level, "Пользователь")
        role_type = "global"
    elif tech_level > 0:
        role = TECH_ROLE_NAMES.get(tech_level, "Технический специалист")
        role_type = "tech"
    else:
        role = "Пользователь"
        role_type = "global"

    return {
        "id": user_id,
        "name": vk_name["full_name"],
        "role": role,
        "role_type": role_type,
        "vk_url": f"https://vk.com/id{user_id}",
        "subtitle": f"ID: {user_id}",
    }


def _load_logs(table_name: str, limit: int, user_id: Optional[int] = None, action_filter: str = "all") -> list[dict]:
    if table_name not in LOG_TABLES:
        abort(404)

    value_column = LOG_TABLES[table_name]["value_column"]
    source_table = "economy" if table_name == "banana_market" else table_name
    where_parts: list[str] = []
    params: list[object] = []
    if user_id is not None:
        where_parts.append("(user_id = ? OR target_id = ?)")
        params.extend([user_id, user_id])

    if table_name == "banana_market":
        if action_filter == "buy":
            where_parts.append("log LIKE ?")
            params.append("%купил(-а)%коинов%")
        elif action_filter == "sell":
            where_parts.append("log LIKE ?")
            params.append("%продал(-а)%коинов%")
        else:
            where_parts.append("(log LIKE ? OR log LIKE ?)")
            params.extend(["%купил(-а)%коинов%", "%продал(-а)%коинов%"])

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    query = (
        f"SELECT rowid, user_id, target_id, {value_column} AS value, log, created_at "
        f"FROM {source_table} {where_clause} ORDER BY rowid DESC LIMIT ?"
    )
    params.append(limit)

    with get_db_connection() as conn:
        raw_rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        rows = []
        for row in raw_rows:
            uid = row.get("user_id")
            tid = row.get("target_id")
            row["actor"] = _build_profile(conn, int(uid)) if uid is not None else _build_profile(conn, None)
            row["target"] = _build_profile(conn, int(tid)) if tid is not None else _build_profile(conn, None)
            row["created_label"] = str(row.get("created_at") or "Дата не записана")
            rows.append(row)
    return rows


def _load_requests(limit: int, status_filter: str) -> list[dict]:
    with get_db_connection() as conn:
        params: list[object] = []
        where = ""
        if status_filter and status_filter != "all":
            where = "WHERE status = ?"
            params.append(status_filter)
        params.append(limit)
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM web_requests {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        ]
        for row in rows:
            row["requester"] = _build_profile(conn, int(row["requester_id"]))
            row["target"] = _build_profile(conn, int(row["target_id"]))
            row["reviewer"] = _build_profile(conn, int(row["reviewer_id"])) if row.get("reviewer_id") else _build_profile(conn, None)
    return rows


def _get_session_user() -> Optional[dict]:
    user_id = session.get("vk_user_id")
    if not user_id:
        return None

    user_id = int(user_id)
    with get_db_connection() as conn:
        global_level = _get_global_level(conn, user_id)
        tech_level = _get_tech_level(conn, user_id)
        profile = _build_profile(conn, user_id)
    return {
        "id": user_id,
        "name": profile["name"],
        "vk_url": profile["vk_url"],
        "global_level": global_level,
        "tech_level": tech_level,
        "role_label": profile["role"],
    }


def _admin_context() -> dict:
    session_user = _get_session_user()
    allowed_by_session = bool(
        session_user and (session_user["tech_level"] > 0 or session_user["global_level"] >= 10)
    )
    return {
        "allowed": allowed_by_session,
        "session_user": session_user,
        "vk_login_ready": bool(VK_APP_ID and VK_APP_ID != "YOUR APP_ID"),
    }


def _require_log_access(admin: dict):
    if not admin["session_user"]:
        if admin["vk_login_ready"]:
            return redirect(url_for("auth_vk_start"))
        return (
            render_template(
                "index.html",
                stats={"page": "botlogs", "limit": DEFAULT_LIMIT, "db_exists": DB_PATH.exists()},
                admin=admin,
                page_title="Доступ к логам",
                page_description="Для доступа к защищённым логам требуется вход через VK.",
                access_denied=True,
                denied_text="Для доступа к защищённым разделам войдите через VK.",
                rows=[],
                request_rows=[],
                request_types=REQUEST_TYPES,
                value_label="",
            ),
            403,
        )
    if not admin["allowed"]:
        return (
            render_template(
                "index.html",
                stats={"page": "botlogs", "limit": DEFAULT_LIMIT, "db_exists": DB_PATH.exists()},
                admin=admin,
                page_title="Доступ к логам",
                page_description="Раздел доступен только техролям, директору и разработчикам.",
                access_denied=True,
                denied_text="У этого аккаунта нет доступа к разделу логов.",
                rows=[],
                request_rows=[],
                request_types=REQUEST_TYPES,
                value_label="",
            ),
            403,
        )
    return None


@app.route("/auth/vk/start")
def auth_vk_start():
    if not VK_APP_ID or VK_APP_ID == "YOUR APP_ID":
        return "В config.json не задан реальный application_id VK.", 500

    callback_url = request.url_root.rstrip("/") + url_for("auth_vk_callback")
    params = {
        "client_id": VK_APP_ID,
        "redirect_uri": callback_url,
        "response_type": "token",
        "scope": "",
        "display": "page",
        "v": VK_API_VERSION,
    }
    return redirect(f"https://oauth.vk.com/authorize?{urllib.parse.urlencode(params)}")


@app.route("/auth/vk/callback")
def auth_vk_callback():
    return render_template("vk_callback.html")


@app.post("/auth/vk/session")
def auth_vk_session():
    payload = request.get_json(silent=True) or {}
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        return jsonify({"ok": False, "error": "Не передан access_token"}), 400

    try:
        query = urllib.parse.urlencode({"access_token": access_token, "v": VK_API_VERSION})
        url = f"https://api.vk.com/method/users.get?{query}"
        with urllib.request.urlopen(url, timeout=10) as response:
            vk_payload = json.loads(response.read().decode("utf-8"))
        response_data = vk_payload.get("response") or []
        if not response_data:
            return jsonify({"ok": False, "error": "VK не вернул данные пользователя"}), 403
        user = response_data[0]
        session["vk_user_id"] = int(user["id"])
        return jsonify({"ok": True, "user_id": int(user["id"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/auth/logout")
def auth_logout():
    session.pop("vk_user_id", None)
    return redirect(url_for("economy_logs_page"))


def _render_logs_page(page_key: str, protected: bool = False):
    ensure_web_schema()
    admin = _admin_context()
    if protected:
        guard = _require_log_access(admin)
        if guard:
            return guard

    limit = _safe_int(request.args.get("limit"), DEFAULT_LIMIT)
    user_filter = _safe_user_id(request.args.get("user_id"))
    action_filter = str(request.args.get("action") or "all").strip().lower()
    if action_filter not in {"all", "buy", "sell"}:
        action_filter = "all"
    meta = LOG_TABLES[page_key]
    rows = _load_logs(page_key, limit, user_filter, action_filter=action_filter)
    stats = {
        "user_filter": user_filter,
        "limit": limit,
        "db_exists": DB_PATH.exists(),
        "action_filter": action_filter,
        "page": "economy" if page_key == "economy" else ("botlogs" if page_key == "logchats" else ("exchangelogs" if page_key == "exchange_logs" else "bananalogs")),
    }
    return render_template(
        "index.html",
        rows=rows,
        request_rows=[],
        request_types=REQUEST_TYPES,
        stats=stats,
        admin=admin,
        page_title=meta["title"],
        page_description=meta["description"],
        value_label=meta["value_label"],
    )


@app.route("/")
@app.route("/economy")
def economy_logs_page():
    return _render_logs_page("economy", protected=False)


@app.route("/botlogs")
def bot_logs_page():
    return _render_logs_page("logchats", protected=True)


@app.route("/exchangelogs")
def exchange_logs_page():
    return _render_logs_page("exchange_logs", protected=True)


@app.route("/bananalogs")
def banana_logs_page():
    return _render_logs_page("banana_market", protected=False)


@app.route("/requests")
def requests_page():
    ensure_web_schema()
    admin = _admin_context()
    guard = _require_log_access(admin)
    if guard:
        return guard

    limit = _safe_int(request.args.get("limit"), DEFAULT_LIMIT)
    status_filter = str(request.args.get("status") or "open").strip().lower()
    request_rows = _load_requests(limit, status_filter)
    stats = {
        "limit": limit,
        "db_exists": DB_PATH.exists(),
        "page": "requests",
        "status_filter": status_filter,
    }
    return render_template(
        "index.html",
        rows=[],
        request_rows=request_rows,
        request_types=REQUEST_TYPES,
        stats=stats,
        admin=admin,
        page_title="Формы действий",
        page_description="Создание и просмотр заявок на обнуление, возврат и другие служебные действия.",
        value_label="",
    )


@app.post("/requests/create")
def create_request():
    ensure_web_schema()
    admin = _admin_context()
    guard = _require_log_access(admin)
    if guard:
        return guard

    requester_id = int(admin["session_user"]["id"])
    target_id = _safe_user_id(request.form.get("target_id"))
    action_type = str(request.form.get("action_type") or "").strip()
    details = str(request.form.get("details") or "").strip()
    amount_raw = str(request.form.get("amount") or "").strip()
    amount = int(amount_raw) if amount_raw.isdigit() else None

    if not target_id or action_type not in {item[0] for item in REQUEST_TYPES}:
        return redirect(url_for("requests_page", status="open", error="invalid"))

    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO web_requests (requester_id, target_id, action_type, amount, details, status, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', CURRENT_TIMESTAMP)
            """,
            (requester_id, target_id, action_type, amount, details),
        )
        conn.commit()
    return redirect(url_for("requests_page", status="open", created="1"))


@app.post("/requests/review")
def review_request():
    """Обработка заявки: принятие или отклонение."""
    ensure_web_schema()
    admin = _admin_context()
    guard = _require_log_access(admin)
    if guard:
        return guard

    request_id = int(request.form.get("request_id") or 0)
    new_status = str(request.form.get("status") or "").strip().lower()
    resolution_note = str(request.form.get("resolution_note") or "").strip()
    reviewer_id = int(admin["session_user"]["id"])

    if not request_id or new_status not in ("approved", "rejected"):
        return redirect(url_for("requests_page", status="open", error="invalid_review"))

    with get_db_connection() as conn:
        # Получаем заявку
        row = conn.execute(
            "SELECT * FROM web_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        
        if not row:
            return redirect(url_for("requests_page", status="open", error="not_found"))

        # Обновляем статус
        conn.execute(
            """
            UPDATE web_requests
            SET status = ?, reviewer_id = ?, resolution_note = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_status, reviewer_id, resolution_note, request_id),
        )
        conn.commit()

        # Если заявка принята - записываем в статистику нормы
        if new_status == "approved":
            requester_id = int(row["requester_id"])
            action_type = str(row["action_type"] or "")
            today = date.today().strftime("%Y-%m-%d")
            
            # Увеличиваем счетчик принятых форм у заявителя
            conn.execute(
                """
                INSERT INTO admin_norms (user_id, norm_date, forms_accepted)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, norm_date) DO UPDATE SET
                forms_accepted = forms_accepted + 1
                """,
                (requester_id, today),
            )
            
            # Если это обнуление - увеличиваем счетчик resets_done у ревьюера (куратора)
            if action_type.startswith("reset_"):
                conn.execute(
                    """
                    INSERT INTO admin_norms (user_id, norm_date, resets_done)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, norm_date) DO UPDATE SET
                    resets_done = resets_done + 1
                    """,
                    (reviewer_id, today),
                )
            
            conn.commit()

    return redirect(url_for("requests_page", status="all", reviewed="1"))




def _runtime_config() -> dict[str, Any]:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _exenta_webhook_secret() -> str:
    current_config = _runtime_config()
    return str(
        os.getenv("EXENTA_WEBHOOK_SECRET")
        or current_config.get("exenta_webhook_secret")
        or ""
    ).strip()


def _verify_exenta_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not raw_body or not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.strip().lower(), expected.lower())


def _run_async(coro):
    return asyncio.run(coro)


def _notify_vk_payment(user_id: int, message_text: str) -> None:
    current_config = _runtime_config()
    token = str(
        os.getenv("VK_BOT_TOKEN")
        or current_config.get("bot-token")
        or current_config.get("bot_token")
        or ""
    ).strip()
    if not token or int(user_id or 0) <= 0:
        return

    payload = urllib.parse.urlencode(
        {
            "peer_id": int(user_id),
            "random_id": 0,
            "message": message_text,
            "access_token": token,
            "v": VK_API_VERSION,
        }
    ).encode("utf-8")
    try:
        vk_request = urllib.request.Request(
            "https://api.vk.com/method/messages.send",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(vk_request, timeout=8) as response:
            response.read()
    except Exception as error:
        print(f"[EXENTA VK NOTIFY ERROR] user_id={user_id}: {error}", flush=True)


@app.get("/payment/success")
def payment_success():
    order = request.args.get("order", "")
    return Response(
        "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h2>✅ Оплата принята</h2>"
        f"<p>Заказ {order}</p>"
        "<p>Вернитесь в сообщения ADMIN MANAGER. Товар будет выдан автоматически.</p>"
        "</body></html>",
        mimetype="text/html",
    )


@app.get("/payment/failed")
def payment_failed():
    order = request.args.get("order", "")
    return Response(
        "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h2>❌ Оплата не завершена</h2>"
        f"<p>Заказ {order}</p>"
        "<p>Вернитесь в магазин и попробуйте снова.</p>"
        "</body></html>",
        mimetype="text/html",
    )


@app.post("/webhooks/exenta")
def exenta_webhook():
    raw_body = request.get_data(cache=True, as_text=False)
    signature = request.headers.get("X-Webhook-Signature", "")
    secret = _exenta_webhook_secret()

    if not secret:
        print("[EXENTA WEBHOOK] EXENTA_WEBHOOK_SECRET is not configured", flush=True)
        return jsonify({"ok": False, "error": "webhook_not_configured"}), 503
    if not _verify_exenta_signature(raw_body, signature, secret):
        print("[EXENTA WEBHOOK] invalid signature", flush=True)
        return jsonify({"ok": False, "error": "invalid_signature"}), 401

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    event = str(
        payload.get("event") or request.headers.get("X-Webhook-Event") or ""
    ).strip().lower()
    if event == "webhook.test":
        return jsonify({"ok": True, "event": "webhook.test"}), 200
    if event != "payment.success":
        return jsonify({"ok": True, "ignored": event}), 200

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    payment_uuid = str(data.get("paymentUuid") or "").strip()
    status = str(data.get("status") or "").strip().upper()
    amount_raw = str(data.get("amount") or "").strip()

    if not payment_uuid or status != "SUCCESS" or not amount_raw.isdigit():
        return jsonify({"ok": False, "error": "invalid_payment_payload"}), 400

    try:
        order = _run_async(get_order_by_payment_uuid(payment_uuid))
    except Exception as error:
        print(f"[EXENTA WEBHOOK] order lookup error uuid={payment_uuid}: {error}", flush=True)
        return jsonify({"ok": False, "error": "order_lookup_failed"}), 500

    if order is None:
        print(f"[EXENTA WEBHOOK] unknown paymentUuid={payment_uuid}", flush=True)
        return jsonify({"ok": False, "error": "order_not_found"}), 404

    expected_amount = int(order["amount_kopecks"])
    actual_amount = int(amount_raw)
    if actual_amount != expected_amount:
        print(
            f"[EXENTA WEBHOOK] amount mismatch uuid={payment_uuid} "
            f"expected={expected_amount} actual={actual_amount}",
            flush=True,
        )
        return jsonify({"ok": False, "error": "amount_mismatch"}), 409

    try:
        success, detail, user_id = _run_async(
            fulfill_exenta_payment(payment_uuid, actual_amount)
        )
    except Exception as error:
        print(f"[EXENTA WEBHOOK] fulfillment exception uuid={payment_uuid}: {error}", flush=True)
        return jsonify({"ok": False, "error": "fulfillment_exception"}), 500

    if not success:
        print(f"[EXENTA WEBHOOK] fulfillment error uuid={payment_uuid}: {detail}", flush=True)
        return jsonify(
            {"ok": False, "error": "fulfillment_failed", "detail": detail}
        ), 500

    if user_id:
        _notify_vk_payment(
            int(user_id),
            "✅ Оплата успешно подтверждена!\n\n"
            f"Заказ: {order['public_id']}\n"
            f"{detail}\n\n"
            "Спасибо за поддержку ADMIN MANAGER 💛",
        )

    print(
        f"[EXENTA WEBHOOK] fulfilled uuid={payment_uuid} "
        f"order={order['public_id']} user={user_id}",
        flush=True,
    )
    return jsonify({"ok": True, "order": order["public_id"]}), 200


@app.route("/health")
def health():
    return {
        "ok": True,
        "web_logs_enabled": ENABLE_WEB_LOGS,
        "database_exists": DB_PATH.exists(),
        "mode": "web",
        "vk_login_ready": bool(VK_APP_ID and VK_APP_ID != "YOUR APP_ID"),
    }


if __name__ == "__main__":
    if not ENABLE_WEB_LOGS:
        print("[web] Web logs are disabled; Exenta webhook and payment pages remain enabled.", flush=True)
    port = int(os.environ.get("PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
