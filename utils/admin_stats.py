import database_pg_async as aiosqlite
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Tuple
from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite

DB_PATH = "database.db"


async def init_admin_stats_tables():
    """Создаёт таблицы для статистики администраторов."""
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            # Таблица для статистики действий
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admin_stats (
                    user_id INTEGER NOT NULL,
                    stat_date TEXT NOT NULL,
                    messages_count INTEGER DEFAULT 0,
                    gbanpl_count INTEGER DEFAULT 0,
                    gban_count INTEGER DEFAULT 0,
                    epoints_earned INTEGER DEFAULT 0,
                    norm_status TEXT DEFAULT 'not_completed',
                    PRIMARY KEY (user_id, stat_date)
                )
            """)
            
            # Таблица для норм (формы, обнуления)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admin_norms (
                    user_id INTEGER NOT NULL,
                    norm_date TEXT NOT NULL,
                    forms_submitted INTEGER DEFAULT 0,
                    forms_accepted INTEGER DEFAULT 0,
                    resets_done INTEGER DEFAULT 0,
                    norm_status TEXT DEFAULT 'not_completed',
                    epoints_reward INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, norm_date)
                )
            """)
            
            # Таблица для настройки чата форм
            await db.execute("""
                CREATE TABLE IF NOT EXISTS af_config (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL
                )
            """)
            
            # Таблица для настройки чата тех. форм (обнуление/восстановление)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS af_tech_config (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL
                )
            """)
            
            # Таблица заявок на обнуление/восстановление
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reset_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    target_user_name TEXT,
                    moderator_id INTEGER NOT NULL,
                    moderator_name TEXT,
                    property_type TEXT NOT NULL,
                    amount TEXT,
                    item_name TEXT,
                    proof_photo TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    answered_by INTEGER,
                    answered_at TEXT,
                    reject_reason TEXT,
                    reason TEXT DEFAULT ''
                )
            """)
            
            # Таблица для永久ного хранения трат Epoints (не удаляется при cleanup)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS epoints_spends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)

            await db.commit()
            print("✅ Таблицы admin_stats, admin_norms и epoints_spends созданы/проверены")
        finally:
            await db.close()


def _get_week_start_date() -> date:
    """Возвращает дату понедельника текущей недели."""
    today = date.today()
    return today - timedelta(days=today.weekday())


def _get_date_key(dt: Optional[datetime] = None) -> str:
    """Возвращает строковое представление даты (YYYY-MM-DD)."""
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d")


async def log_admin_action(
    user_id: int,
    action_type: str,
    stat_date: Optional[str] = None
) -> None:
    """
    Записывает действие администратора для статистики.
    
    action_type: 'gbanpl', 'gban', 'message'
    """
    if stat_date is None:
        stat_date = _get_date_key()
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            if action_type == 'gbanpl':
                await db.execute(
                    """
                    INSERT INTO admin_stats (user_id, stat_date, gbanpl_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, stat_date) DO UPDATE SET
                    gbanpl_count = admin_stats.gbanpl_count + excluded.gbanpl_count
                    """,
                    (user_id, stat_date),
                )
            elif action_type == 'gban':
                await db.execute(
                    """
                    INSERT INTO admin_stats (user_id, stat_date, gban_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, stat_date) DO UPDATE SET
                    gban_count = admin_stats.gban_count + excluded.gban_count
                    """,
                    (user_id, stat_date),
                )
            elif action_type == 'message':
                await db.execute(
                    """
                    INSERT INTO admin_stats (user_id, stat_date, messages_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, stat_date) DO UPDATE SET
                    messages_count = admin_stats.messages_count + excluded.messages_count
                    """,
                    (user_id, stat_date),
                )
            await db.commit()
        finally:
            await db.close()


async def add_epoints_to_stat(
    user_id: int,
    epoints: int,
    stat_date: Optional[str] = None
) -> None:
    """Добавляет Epoints к статистике администратора."""
    if stat_date is None:
        stat_date = _get_date_key()
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO admin_stats (user_id, stat_date, epoints_earned)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, stat_date) DO UPDATE SET
                epoints_earned = admin_stats.epoints_earned + excluded.epoints_earned
                """,
                (user_id, stat_date, epoints),
            )
            await db.commit()
        finally:
            await db.close()


async def get_admin_stat(
    user_id: int,
    stat_date: Optional[str] = None
) -> Optional[Dict]:
    """Получает статистику администратора за указанную дату."""
    if stat_date is None:
        stat_date = _get_date_key()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT user_id, stat_date, messages_count, gbanpl_count, 
                   gban_count, epoints_earned, norm_status
            FROM admin_stats
            WHERE user_id = ? AND stat_date = ?
            """,
            (user_id, stat_date),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_admin_stat_week(
    user_id: int,
    week_start: Optional[date | str] = None
) -> Dict:
    """Получает aggregated статистику за неделю."""
    if week_start is None:
        week_start = _get_week_start_date()
    elif isinstance(week_start, str):
        week_start = date.fromisoformat(week_start)
    
    week_end = week_start + timedelta(days=6)
    
    week_end = week_start + timedelta(days=6)
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT 
                COALESCE(SUM(messages_count), 0) as messages_count,
                COALESCE(SUM(gbanpl_count), 0) as gbanpl_count,
                COALESCE(SUM(gban_count), 0) as gban_count,
                COALESCE(SUM(epoints_earned), 0) as epoints_earned
            FROM admin_stats
            WHERE user_id = ? AND stat_date BETWEEN ? AND ?
            """,
            (user_id, week_start_str, week_end_str),
        )
        row = await cur.fetchone()
        return dict(row) if row else {
            'messages_count': 0,
            'gbanpl_count': 0,
            'gban_count': 0,
            'epoints_earned': 0,
        }


async def get_admin_stat_all_time(user_id: int) -> Dict:
    """Получает общую статистику за всё время."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT 
                COALESCE(SUM(messages_count), 0) as messages_count,
                COALESCE(SUM(gbanpl_count), 0) as gbanpl_count,
                COALESCE(SUM(gban_count), 0) as gban_count,
                COALESCE(SUM(epoints_earned), 0) as epoints_earned
            FROM admin_stats
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else {
            'messages_count': 0,
            'gbanpl_count': 0,
            'gban_count': 0,
            'epoints_earned': 0,
        }


async def get_admin_norm_totals(
    user_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict:
    """Returns aggregated norm counters for a user."""
    query = """
        SELECT
            COALESCE(SUM(forms_submitted), 0) as forms_submitted,
            COALESCE(SUM(forms_accepted), 0) as forms_accepted,
            COALESCE(SUM(resets_done), 0) as resets_done
        FROM admin_norms
        WHERE user_id = ?
    """
    params: list = [user_id]
    if start_date is not None and end_date is not None:
        query += " AND norm_date BETWEEN ? AND ?"
        params.extend([start_date, end_date])

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, tuple(params))
        row = await cur.fetchone()
        return dict(row) if row else {
            'forms_submitted': 0,
            'forms_accepted': 0,
            'resets_done': 0,
        }


async def get_admin_stats_by_date_range(
    user_id: int,
    start_date: str,
    end_date: str
) -> List[Dict]:
    """Получает статистику за диапазон дат."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT user_id, stat_date, messages_count, gbanpl_count,
                   gban_count, epoints_earned, norm_status
            FROM admin_stats
            WHERE user_id = ? AND stat_date BETWEEN ? AND ?
            ORDER BY stat_date DESC
            """,
            (user_id, start_date, end_date),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]


async def update_norm_status(
    user_id: int,
    stat_date: str,
    norm_status: str,
    epoints_reward: int = 0
) -> None:
    """Обновляет статус нормы и награду."""
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO admin_stats (user_id, stat_date, norm_status, epoints_earned)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, stat_date) DO UPDATE SET
                norm_status = excluded.norm_status,
                epoints_earned = admin_stats.epoints_earned + excluded.epoints_earned
                """,
                (user_id, stat_date, norm_status, epoints_reward),
            )
            await db.commit()
        finally:
            await db.close()


async def cleanup_old_admin_stats(weeks_to_keep: int = 4) -> None:
    """Удаляет статистику старше указанного количества недель."""
    cutoff_date = date.today() - timedelta(weeks=weeks_to_keep)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "DELETE FROM admin_stats WHERE stat_date < ?",
                (cutoff_str,),
            )
            await db.commit()
        finally:
            await db.close()


async def reset_weekly_admin_stats() -> None:
    """Удаляет статистику за прошлую неделю (перед текущим понедельником)."""
    week_start = _get_week_start_date()
    week_start_str = week_start.strftime("%Y-%m-%d")
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "DELETE FROM admin_stats WHERE stat_date < ?",
                (week_start_str,),
            )
            await db.commit()
        finally:
            await db.close()


async def get_admin_norm(user_id: int, norm_date: Optional[str] = None) -> Optional[Dict]:
    """Получает информацию о норме администратора."""
    if norm_date is None:
        norm_date = _get_date_key()
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT user_id, norm_date, forms_submitted, forms_accepted,
                   resets_done, norm_status, epoints_reward
            FROM admin_norms
            WHERE user_id = ? AND norm_date = ?
            """,
            (user_id, norm_date),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_admin_norm(
    user_id: int,
    norm_date: str,
    forms_submitted: int = 0,
    forms_accepted: int = 0,
    resets_done: int = 0,
    norm_status: str = 'not_completed',
    epoints_reward: int = 0
) -> None:
    """Обновляет или создает запись о норме."""
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO admin_norms (user_id, norm_date, forms_submitted, 
                                         forms_accepted, resets_done, norm_status, epoints_reward)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, norm_date) DO UPDATE SET
                forms_submitted = admin_norms.forms_submitted + excluded.forms_submitted,
                forms_accepted = admin_norms.forms_accepted + excluded.forms_accepted,
                resets_done = admin_norms.resets_done + excluded.resets_done,
                norm_status = excluded.norm_status,
                epoints_reward = excluded.epoints_reward
                """,
                (user_id, norm_date, forms_submitted, forms_accepted, 
                 resets_done, norm_status, epoints_reward),
            )
            await db.commit()
        finally:
            await db.close()


async def calculate_and_set_norm(
    user_id: int,
    role: int,
    norm_date: Optional[str] = None
) -> Tuple[str, int]:
    """
    Рассчитывает и устанавливает статус нормы.
    
    === РОЛИ ===
    Локальные (0-7): Нет нормы
    8: Медиа Партнер - Нет нормы
    9: Технический специалист - Отправка форм + сообщения
    10: Заместитель директора - Норма по активности
    11: Осн. заместитель директора - Норма по активности
    12: Куратор техспециалистов - Приём форм + обнуления
    13: Зам. Главного Тех. Специалиста - Приём форм + обнуления
    14: Главный Тех. Специалист - Приём форм + обнуления
    15: Директор бота - Норма по активности
    16: Разработчик бота - Индивидуальная (500 Epoints)
    
    Возвращает: (norm_status, epoints_reward)
    """
    if norm_date is None:
        norm_date = _get_date_key()
    
    # Роли 0-8 не имеют нормы
    if role < 9:
        return 'no_norm', 0
    
    norm_info = await get_admin_norm(user_id, norm_date)
    if norm_info:
        forms_accepted = int(norm_info.get('forms_accepted', 0) or 0)
        resets_done = int(norm_info.get('resets_done', 0) or 0)
        forms_submitted = int(norm_info.get('forms_submitted', 0) or 0)
    else:
        forms_accepted = 0
        resets_done = 0
        forms_submitted = 0
    
    # Получаем статистику за сегодня
    today_stat = await get_admin_stat(user_id, norm_date)
    if not today_stat:
        messages_count = 0
        gbanpl_count = 0
        gban_count = 0
    else:
        messages_count = int(today_stat.get('messages_count', 0) or 0)
        gbanpl_count = int(today_stat.get('gbanpl_count', 0) or 0)
        gban_count = int(today_stat.get('gban_count', 0) or 0)
    
    norm_status = 'not_completed'
    epoints_reward = 0
    
    # === ТЕХНИЧЕСКИЙ СОСТАВ ===
    if role == 9:  # Технический специалист - ФОРМЫ + СООБЩЕНИЯ
        if forms_submitted >= 2 and messages_count >= 100:
            norm_status = 'completed'
            epoints_reward = 100
        elif forms_submitted >= 1 and messages_count >= 50:
            norm_status = 'stretched'
            epoints_reward = 50
    
    elif role == 10:  # Заместитель директора
        if messages_count >= 600:
            norm_status = 'completed'
            epoints_reward = 300
        elif messages_count >= 300:
            norm_status = 'stretched'
            epoints_reward = 150
    
    elif role == 11:  # Осн. заместитель директора
        if messages_count >= 400:
            norm_status = 'completed'
            epoints_reward = 400
        elif messages_count >= 200:
            norm_status = 'stretched'
            epoints_reward = 200
    
    elif role == 12:  # Куратор техспециалистов
        if forms_accepted >= 3 and resets_done >= 1:
            norm_status = 'completed'
            epoints_reward = 150
        elif forms_accepted >= 1:
            norm_status = 'stretched'
            epoints_reward = 75
    
    elif role == 13:  # Заместитель Главного Тех. Специалиста
        if forms_accepted >= 5 and resets_done >= 2:
            norm_status = 'completed'
            epoints_reward = 350
        elif forms_accepted >= 2 and resets_done >= 1:
            norm_status = 'stretched'
            epoints_reward = 175
    
    elif role == 14:  # Главный Тех. Специалист
        if forms_accepted >= 8 and resets_done >= 3:
            norm_status = 'completed'
            epoints_reward = 450
        elif forms_accepted >= 4 and resets_done >= 1:
            norm_status = 'stretched'
            epoints_reward = 225
    
    elif role == 15:  # Директор бота
        if messages_count >= 200:
            norm_status = 'completed'
            epoints_reward = 500
        elif messages_count >= 100:
            norm_status = 'stretched'
            epoints_reward = 250
    
    elif role == 16:  # Контроль качества
        if messages_count >= 200:
            norm_status = 'completed'
            epoints_reward = 500
        elif messages_count >= 100:
            norm_status = 'stretched'
            epoints_reward = 250

    elif role == 17:  # Разработчик бота
        if messages_count >= 1:
            norm_status = 'completed'
            epoints_reward = 500
    
    # Обновляем запись с наградой
    # Сохраняем предыдущий статус нормы чтобы проверить переход
    old_norm_info = await get_admin_norm(user_id, norm_date)
    old_status = old_norm_info.get('norm_status', 'not_completed') if old_norm_info else 'not_completed'
    old_reward = int(old_norm_info.get('epoints_reward', 0) or 0) if old_norm_info else 0

    await update_admin_norm(
        user_id=user_id,
        norm_date=norm_date,
        norm_status=norm_status,
        epoints_reward=epoints_reward
    )

    # Начисляем epoints только при переходе not_completed -> completed/stretched
    # и только если награда выросла (чтобы не начислять повторно при каждом просмотре)
    if norm_status in ('completed', 'stretched') and epoints_reward > 0:
        new_epoints = epoints_reward - old_reward
        if new_epoints > 0:
            await add_epoints_to_stat(user_id, new_epoints, norm_date)

    return norm_status, epoints_reward


async def _get_existing_norm_progress(user_id: int, norm_date: str) -> Tuple[str, int]:
    norm_info = await get_admin_norm(user_id, norm_date)
    if not norm_info:
        return 'not_completed', 0
    return (
        norm_info.get('norm_status', 'not_completed') or 'not_completed',
        int(norm_info.get('epoints_reward', 0) or 0),
    )


async def increment_forms_submitted(user_id: int, norm_date: Optional[str] = None) -> None:
    """Увеличивает счетчик отправленных форм."""
    if norm_date is None:
        norm_date = _get_date_key()
    norm_status, epoints_reward = await _get_existing_norm_progress(user_id, norm_date)
    await update_admin_norm(
        user_id,
        norm_date,
        forms_submitted=1,
        norm_status=norm_status,
        epoints_reward=epoints_reward,
    )


async def increment_forms_accepted(user_id: int, norm_date: Optional[str] = None) -> None:
    """Увеличивает счетчик принятых форм."""
    if norm_date is None:
        norm_date = _get_date_key()
    norm_status, epoints_reward = await _get_existing_norm_progress(user_id, norm_date)
    await update_admin_norm(
        user_id,
        norm_date,
        forms_accepted=1,
        norm_status=norm_status,
        epoints_reward=epoints_reward,
    )


async def increment_resets_done(user_id: int, norm_date: Optional[str] = None) -> None:
    """Увеличивает счетчик обнулений."""
    if norm_date is None:
        norm_date = _get_date_key()
    norm_status, epoints_reward = await _get_existing_norm_progress(user_id, norm_date)
    await update_admin_norm(
        user_id,
        norm_date,
        resets_done=1,
        norm_status=norm_status,
        epoints_reward=epoints_reward,
    )


async def get_admin_stats_last_days(user_id: int, days: int = 7) -> List[Dict]:
    """Получает статистику за последние N дней с данными."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT stat_date, messages_count, gbanpl_count, gban_count, epoints_earned
            FROM admin_stats
            WHERE user_id = ?
            ORDER BY stat_date DESC
            LIMIT ?
            """,
            (user_id, days),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]


async def get_total_epoints(user_id: int) -> int:
    """Получает текущий баланс Epoints (начисления минус траты)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT COALESCE(SUM(epoints_earned), 0) as total_earned
            FROM admin_stats
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        total_earned = int(row['total_earned']) if row else 0

        cur2 = await db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as total_spent
            FROM epoints_spends
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row2 = await cur2.fetchone()
        total_spent = int(row2['total_spent']) if row2 else 0

        return total_earned - total_spent


async def spend_epoints(user_id: int, amount: int, reason: str = "") -> bool:
    """Тратит Epoints. Записывает в permanentную таблицу epoints_spends."""
    total = await get_total_epoints(user_id)
    if total < amount:
        return False
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await db.execute(
                """
                INSERT INTO epoints_spends (user_id, amount, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, amount, reason, created_at),
            )
            await db.commit()
        finally:
            await db.close()
    return True


# ============================================================
# Функции для работы с заявками на обнуление/восстановление
# ============================================================

async def ensure_reset_requests_reason_column():
    """Добавляет колонку reason если её нет (для существующих БД)."""
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            cursor = await db.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'reset_requests'")
            columns = [row[0] for row in await cursor.fetchall()]
            if 'reason' not in columns:
                await db.execute("ALTER TABLE reset_requests ADD COLUMN reason TEXT DEFAULT ''")
                await db.commit()
                print("✅ Добавлена колонка reason в reset_requests")
        except Exception as e:
            print(f"[DB] ensure_reset_requests_reason_column: {e}")
        finally:
            await db.close()

async def get_total_reset_requests_count() -> int:
    """Получает общее количество заявок."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reset_requests")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

async def create_reset_request(
    target_user_id: int,
    target_user_name: str,
    moderator_id: int,
    moderator_name: str,
    property_type: str,
    amount: str = "",
    item_name: str = "",
    proof_photo: str = "",
    reason: str = ""
) -> str:
    """Создаёт заявку на обнуление/восстановление и возвращает её ID."""
    import uuid
    request_id = str(uuid.uuid4())[:8].upper()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                INSERT INTO reset_requests 
                (request_id, target_user_id, target_user_name, moderator_id, moderator_name,
                 property_type, amount, item_name, proof_photo, status, created_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (request_id, target_user_id, target_user_name, moderator_id, moderator_name,
                 property_type, amount, item_name, proof_photo, created_at, reason)
            )
            await db.commit()
            return request_id
        finally:
            await db.close()


async def get_reset_request(request_id: str) -> Optional[Dict]:
    """Получает заявку по ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reset_requests WHERE request_id = ?",
            (request_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_pending_reset_requests(status: str = "pending") -> List[Dict]:
    """Получает все заявки с указанным статусом."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reset_requests WHERE status = ? ORDER BY created_at DESC",
            (status,)
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]


async def approve_reset_request(request_id: str, answered_by: int) -> bool:
    """Одобряет заявку на обнуление/восстановление."""
    answered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                UPDATE reset_requests 
                SET status = 'approved', answered_by = ?, answered_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (answered_by, answered_at, request_id)
            )
            await db.commit()
            return True
        finally:
            await db.close()


async def reject_reset_request(request_id: str, answered_by: int, reason: str = "") -> bool:
    """Отклоняет заявку на обнуление/восстановление."""
    answered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                """
                UPDATE reset_requests 
                SET status = 'rejected', answered_by = ?, answered_at = ?, reject_reason = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (answered_by, answered_at, reason, request_id)
            )
            await db.commit()
            return True
        finally:
            await db.close()


async def get_reset_request_by_id(request_id: int) -> Optional[Dict]:
    """Получает заявку по внутреннему ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reset_requests WHERE id = ?",
            (request_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# Типы имущества для заявок
RESET_PROPERTY_TYPES = [
    ("balance", "💰 Баланс"),
    ("bank", "🏦 Банк"),
    ("vip", "⭐ VIP"),
    ("business", "🏢 Бизнесы"),
    ("cases", "📦 Кейсы"),
    ("bananas", "🪙 Коины"),
    ("inventory", "🎒 Инвентарь"),
    ("items", "🎁 Предметы"),
]


def get_property_type_name(property_type: str) -> str:
    """Возвращает название типа имущества."""
    if property_type == "all":
        return "ВСЁ имущество"
    for ptype, pname in RESET_PROPERTY_TYPES:
        if ptype == property_type:
            return pname
    return property_type
