from datetime import datetime, time, timedelta

from utils.pass_runtime import get_pass_profile
from utils.pass_system import (
    CURRENT_PASS_SEASON,
    MSK_TZ,
    PASS_QUEST_POOL,
    POST_PASS_SPECIAL_QUEST_POOL,
    PREMIUM_PASS_REWARDS,
    STANDARD_PASS_REWARDS,
    WEEKLY_PASS_QUEST_POOL,
    _now_msk,
    _season_end_left_text,
    _today_str,
    format_reward_short,
)


def _resolve_daily_meta(quest_id: str) -> dict:
    meta = PASS_QUEST_POOL.get(str(quest_id))
    if meta:
        return meta
    legacy_aliases = {
        "collect_business_income_5": {"title": "Соберите доход с бизнесов 5 раз"},
        "duel_create_1": {"title": "Сыграйте 1 дуэль на 10.000.000₽"},
        "duel_create_2": {"title": "Сыграйте 2 дуэли на 10.000.000₽"},
        "duel_create_4": {"title": "Сыграйте 4 дуэли на 10.000.000₽"},
    }
    return legacy_aliases.get(str(quest_id), {"title": str(quest_id)})


def _resolve_weekly_meta(quest_id: str) -> dict:
    meta = WEEKLY_PASS_QUEST_POOL.get(str(quest_id))
    if meta:
        return meta
    legacy_aliases = {
        "collect_business_income_5": {"title": "Соберите доход с бизнесов 5 раз"},
    }
    return legacy_aliases.get(str(quest_id), {"title": str(quest_id)})


def _resolve_special_meta(quest_id: str) -> dict:
    return POST_PASS_SPECIAL_QUEST_POOL.get(str(quest_id), {"title": str(quest_id), "reward": {"title": "Награда"}})


def _format_delta_text(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _seconds = divmod(remainder, 60)
    return f"{hours}ч. {minutes}м."


def _daily_refresh_left_text() -> str:
    now = _now_msk()
    next_day = now.date() + timedelta(days=1)
    refresh_at = MSK_TZ.localize(datetime.combine(next_day, time.min))
    return _format_delta_text(refresh_at - now)


def _weekly_refresh_left_text() -> str:
    now = _now_msk()
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = now.date() + timedelta(days=days_until_monday)
    refresh_at = MSK_TZ.localize(datetime.combine(next_monday, time.min))
    delta = refresh_at - now
    total_seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _seconds = divmod(remainder, 60)
    return f"{days}д. {hours}ч. {minutes}м."


def _daily_refresh_info_text() -> str:
    now = _now_msk()
    next_day = now.date() + timedelta(days=1)
    refresh_at = MSK_TZ.localize(datetime.combine(next_day, time.min))
    return f"{refresh_at.strftime('%d.%m %H:%M')} МСК (через {_format_delta_text(refresh_at - now)})"


def _weekly_refresh_info_text() -> str:
    now = _now_msk()
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = now.date() + timedelta(days=days_until_monday)
    refresh_at = MSK_TZ.localize(datetime.combine(next_monday, time.min))
    return f"{refresh_at.strftime('%d.%m %H:%M')} МСК (через {_weekly_refresh_left_text()})"


async def build_pass_text(user_id: int) -> str:
    profile = await get_pass_profile(user_id)
    xp = int(profile["xp"])
    level = int(profile["level"])
    unlocked = int(profile["unlocked_levels"])
    xp_per_level = int(CURRENT_PASS_SEASON["xp_per_level"])
    progress_in_level = xp % xp_per_level
    claims = profile["claims"]
    standard_available = sum(
        1
        for lvl in range(1, unlocked + 1)
        if lvl in STANDARD_PASS_REWARDS and (lvl, "standard") not in claims
    )
    premium_available = sum(
        1
        for lvl in range(1, unlocked + 1)
        if lvl in PREMIUM_PASS_REWARDS and (lvl, "premium") not in claims
    )
    premium_status = (
        "активен только в текущем сезоне"
        if profile["premium_active"]
        else f"не активен ({CURRENT_PASS_SEASON['premium_cost_bananas']} коинов)"
    )
    preview_start = max(1, min(unlocked + 1, CURRENT_PASS_SEASON["max_level"]))
    preview_end = min(CURRENT_PASS_SEASON["max_level"], preview_start + 4)

    lines = [
        f"🪙 {CURRENT_PASS_SEASON['title']}",
        CURRENT_PASS_SEASON["subtitle"],
        "",
        "🪖 Статус:",
        f"🕯 До конца сезона: {_season_end_left_text()}",
        f"🏁 Уровень пропуска: {level}/{CURRENT_PASS_SEASON['max_level']}",
        f"⭐ XP: {xp}",
        f"📈 Прогресс уровня: {progress_in_level}/{xp_per_level}",
        "🎟 Standart: активен",
        f"🕯 Premium: {premium_status}",
        f"🧭 Следующий сезон: {CURRENT_PASS_SEASON['next_season_title']}",
        "",
        "🎁 Доступные награды:",
        f"🎁 Standart: {standard_available}",
        f"🎁 Premium: {premium_available}",
        "",
        "🔥 Ближайшие уровни:",
    ]
    if profile.get("post_pass_complete"):
        lines.extend([
            "🏆 Вы успешно прошли все уровни ADMIN PASS.",
            "🎯 Вместо обычных заданий вам доступны 5 специальных ежедневных квестов.",
            "🎁 Награды по ним выдаются сразу и не добавляют лишний XP сверх капа.",
            "",
        ])
    for lvl in range(preview_start, preview_end + 1):
        lines.append(f"{lvl}. S: {format_reward_short('standard', lvl)}")
        lines.append(f"{lvl}. P: {format_reward_short('premium', lvl)}")
    lines.append("")
    lines.append("📌 Команды: /pass, /квесты, /pass premium, /pass claim")
    return "\n".join(lines)


async def build_quests_text(user_id: int) -> str:
    profile = await get_pass_profile(user_id)
    if profile.get("post_pass_complete"):
        special = profile.get("special_quests", [])
        completed_special = sum(1 for quest in special if int(quest.get("completed", 0) or 0) == 1)
        daily_refresh_info = _daily_refresh_info_text()
        lines = [
            "🗂 Задания ADMIN PASS",
            f"📅 Сегодня: {_today_str()}",
            f"🕯 До конца сезона: {_season_end_left_text()}",
            "",
            "🏆 Вы успешно прошли все уровни ADMIN PASS.",
            f"🎁 Специальные квесты: {completed_special}/{len(special)}",
            f"⏱ Обновление специальных квестов: {daily_refresh_info}",
        ]
        for quest in special:
            meta = _resolve_special_meta(str(quest["quest_id"]))
            done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "★"
            reward_title = meta.get("reward", {}).get("title", "Награда")
            lines.append(
                f"{done} {meta.get('title', quest['quest_id'])} | "
                f"{quest['progress']}/{quest['target']} | Награда: {reward_title}"
            )
        return "\n".join(lines)

    regular = [quest for quest in profile["quests"] if int(quest.get("is_elite", 0) or 0) == 0]
    elite = [quest for quest in profile["quests"] if int(quest.get("is_elite", 0) or 0) == 1]
    weekly = profile.get("weekly_quests", [])
    completed_regular = sum(1 for quest in regular if int(quest.get("completed", 0) or 0) == 1)
    completed_elite = sum(1 for quest in elite if int(quest.get("completed", 0) or 0) == 1)
    completed_weekly = sum(1 for quest in weekly if int(quest.get("completed", 0) or 0) == 1)

    daily_refresh_info = _daily_refresh_info_text()
    weekly_refresh_info = _weekly_refresh_info_text()

    lines = [
        "🗂 Задания ADMIN PASS",
        f"📅 Сегодня: {_today_str()}",
        f"🕯 До конца сезона: {_season_end_left_text()}",
        "",
        f"🎟 Обычные задания: {completed_regular}/{len(regular)}",
        f"⏱ Обновление: {daily_refresh_info}",
    ]
    for quest in regular:
        meta = _resolve_daily_meta(str(quest["quest_id"]))
        done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "•"
        lines.append(
            f"{done} {meta.get('title', quest['quest_id'])} | "
            f"{quest['progress']}/{quest['target']} | {quest['xp_reward']} XP"
        )

    if elite:
        lines.append("")
        lines.append(f"🔥 Элитные задания: {completed_elite}/{len(elite)}")
        lines.append(f"⏱ Обновление: {daily_refresh_info}")
    for quest in elite:
        meta = _resolve_daily_meta(str(quest["quest_id"]))
        done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "★"
        lines.append(
            f"{done} {meta.get('title', quest['quest_id'])} | "
            f"{quest['progress']}/{quest['target']} | {quest['xp_reward']} XP"
        )

    if weekly:
        lines.append("")
        lines.append(f"📆 Недельные задания: {completed_weekly}/{len(weekly)}")
        lines.append(f"⏱ Обновление: {weekly_refresh_info}")
    for quest in weekly:
        meta = _resolve_weekly_meta(str(quest["quest_id"]))
        done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "◆"
        lines.append(
            f"{done} {meta.get('title', quest['quest_id'])} | "
            f"{quest['progress']}/{quest['target']} | {quest['xp_reward']} XP"
        )
    return "\n".join(lines)
