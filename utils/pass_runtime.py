import asyncio
from typing import Dict, List

from utils.pass_system import (
    CURRENT_PASS_SEASON,
    DB_WRITE_LOCK,
    PASS_QUEST_POOL,
    POST_PASS_SPECIAL_QUEST_POOL,
    WEEKLY_PASS_QUEST_POOL,
    award_pass_reward,
    _today_str,
    _week_key,
    connect_sqlite,
    ensure_daily_quests,
    ensure_pass_user,
    ensure_special_quests,
    ensure_weekly_quests,
    get_claimed_rewards_map,
    get_level_from_xp,
    get_unlocked_level_count,
    is_post_pass_complete,
    is_pass_active,
)


async def get_pass_profile(user_id: int) -> Dict:
    try:
        user_data = await asyncio.wait_for(ensure_pass_user(user_id), timeout=1.5)
        xp = int(user_data.get("xp", 0) or 0)
        post_pass_complete = is_post_pass_complete(xp)
        quests = [] if post_pass_complete else await asyncio.wait_for(ensure_daily_quests(user_id), timeout=1.5)
        weekly_quests = [] if post_pass_complete else await asyncio.wait_for(ensure_weekly_quests(user_id), timeout=1.5)
        special_quests = await asyncio.wait_for(ensure_special_quests(user_id), timeout=1.5) if post_pass_complete else []
        claims = await asyncio.wait_for(get_claimed_rewards_map(user_id), timeout=1.5)
        return {
            "season": CURRENT_PASS_SEASON,
            "xp": xp,
            "level": get_level_from_xp(xp),
            "unlocked_levels": get_unlocked_level_count(xp),
            "premium_active": bool(int(user_data.get("premium_active", 0) or 0)),
            "quests": quests,
            "weekly_quests": weekly_quests,
            "special_quests": special_quests,
            "post_pass_complete": post_pass_complete,
            "claims": claims,
            "active": is_pass_active(),
        }
    except asyncio.TimeoutError:
        return {
            "season": CURRENT_PASS_SEASON,
            "xp": 0,
            "level": 1,
            "unlocked_levels": 0,
            "premium_active": False,
            "quests": [],
            "weekly_quests": [],
            "special_quests": [],
            "post_pass_complete": False,
            "claims": {},
            "active": is_pass_active(),
        }


async def record_pass_progress(user_id: int, event_type: str, amount: int = 1, bot=None) -> List[str]:
    if not is_pass_active() or amount <= 0:
        return []

    profile = await get_pass_profile(user_id)
    if profile.get("post_pass_complete"):
        special_quests = profile.get("special_quests", [])
        completed_messages: List[str] = []
        pending_rewards: List[Dict] = []
        season_id = CURRENT_PASS_SEASON["id"]
        quest_date = _today_str()
        async with DB_WRITE_LOCK:
            db = await connect_sqlite()
            try:
                for quest in special_quests:
                    quest_meta = POST_PASS_SPECIAL_QUEST_POOL.get(str(quest["quest_id"]))
                    if not quest_meta or quest_meta["event"] != event_type or int(quest.get("completed", 0) or 0) == 1:
                        continue
                    new_progress = min(int(quest["target"]), int(quest["progress"]) + int(amount))
                    completed = 1 if new_progress >= int(quest["target"]) else 0
                    rewarded = int(quest.get("rewarded", 0) or 0)
                    if completed and not rewarded:
                        rewarded = 2
                        pending_rewards.append(
                            {
                                "slot": int(quest["slot"]),
                                "title": quest_meta["title"],
                                "reward": quest_meta["reward"],
                            }
                        )
                    await db.execute(
                        """
                        UPDATE banana_pass_special_quests
                        SET progress = ?, completed = ?, rewarded = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ? AND season_id = ? AND quest_date = ? AND slot = ?
                        """,
                        (new_progress, completed, rewarded, user_id, season_id, quest_date, int(quest["slot"])),
                    )
                await db.commit()
            finally:
                await db.close()
        for pending_reward in pending_rewards:
            reward_text = await award_pass_reward(user_id, pending_reward["reward"])
            completed_messages.append(
                f"✅ ADMIN PASS: СПЕЦКВЕСТ ВЫПОЛНЕН\n{pending_reward['title']}\nНаграда: {reward_text}"
            )
        if pending_rewards:
            async with DB_WRITE_LOCK:
                db = await connect_sqlite()
                try:
                    for pending_reward in pending_rewards:
                        await db.execute(
                            """
                            UPDATE banana_pass_special_quests
                            SET rewarded = 1, updated_at = CURRENT_TIMESTAMP
                            WHERE user_id = ? AND season_id = ? AND quest_date = ? AND slot = ? AND rewarded = 2
                            """,
                            (user_id, season_id, quest_date, int(pending_reward["slot"])),
                        )
                    await db.commit()
                finally:
                    await db.close()
        if bot and completed_messages:
            for text in completed_messages:
                try:
                    await bot.api.messages.send(peer_id=user_id, random_id=0, message=text)
                except Exception:
                    pass
        return completed_messages

    quests = await ensure_daily_quests(user_id)
    weekly_quests = await ensure_weekly_quests(user_id)
    completed_messages: List[str] = []
    xp_total = 0
    season_id = CURRENT_PASS_SEASON["id"]
    quest_date = _today_str()
    week_key = _week_key()

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            for quest in quests:
                quest_meta = PASS_QUEST_POOL.get(str(quest["quest_id"]))
                if not quest_meta or quest_meta["event"] != event_type or int(quest.get("completed", 0) or 0) == 1:
                    continue
                new_progress = min(int(quest["target"]), int(quest["progress"]) + int(amount))
                completed = 1 if new_progress >= int(quest["target"]) else 0
                await db.execute(
                    """
                    UPDATE banana_pass_daily_quests
                    SET progress = ?, completed = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND season_id = ? AND quest_date = ? AND slot = ?
                    """,
                    (new_progress, completed, user_id, season_id, quest_date, int(quest["slot"])),
                )
                if completed:
                    xp_total += int(quest["xp_reward"])
                    quest_type_label = "ЭЛИТНЫЙ" if int(quest.get("is_elite", 0) or 0) == 1 else "ЕЖЕДНЕВНЫЙ"
                    completed_messages.append(
                        f"✅ ADMIN PASS: {quest_type_label} КВЕСТ ВЫПОЛНЕН\n{quest_meta['title']}\nНаграда: {quest['xp_reward']} XP"
                    )

            for quest in weekly_quests:
                quest_meta = WEEKLY_PASS_QUEST_POOL.get(str(quest["quest_id"]))
                if not quest_meta or quest_meta["event"] != event_type or int(quest.get("completed", 0) or 0) == 1:
                    continue
                new_progress = min(int(quest["target"]), int(quest["progress"]) + int(amount))
                completed = 1 if new_progress >= int(quest["target"]) else 0
                await db.execute(
                    """
                    UPDATE banana_pass_weekly_quests
                    SET progress = ?, completed = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND season_id = ? AND week_key = ? AND slot = ?
                    """,
                    (new_progress, completed, user_id, season_id, week_key, int(quest["slot"])),
                )
                if completed:
                    xp_total += int(quest["xp_reward"])
                    completed_messages.append(
                        f"✅ ADMIN PASS: НЕДЕЛЬНЫЙ КВЕСТ ВЫПОЛНЕН\n{quest_meta['title']}\nНаграда: {quest['xp_reward']} XP"
                    )

            if xp_total > 0:
                await db.execute(
                    "UPDATE banana_pass_users SET xp = xp + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND season_id = ?",
                    (xp_total, user_id, season_id),
                )
            await db.commit()
        finally:
            await db.close()

    if bot and completed_messages:
        for text in completed_messages:
            try:
                await bot.api.messages.send(peer_id=user_id, random_id=0, message=text)
            except Exception:
                pass
    return completed_messages
