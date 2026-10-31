from utils.pass_runtime import get_pass_profile
from utils.pass_system import CURRENT_PASS_SEASON, PREMIUM_PASS_REWARDS, STANDARD_PASS_REWARDS


async def build_pass_levels_text(user_id: int, track: str) -> str:
    profile = await get_pass_profile(user_id)
    track_normalized = str(track).lower()
    if track_normalized not in {"standard", "standart", "premium"}:
        track_normalized = "standard"

    if track_normalized == "premium" and not profile["premium_active"]:
        return (
            "👑 Список уровней Premium недоступен.\n"
            "Сначала купите Premium ADMIN PASS текущего сезона, после чего вам будет доступен полный список уровней."
        )

    reward_map = PREMIUM_PASS_REWARDS if track_normalized == "premium" else STANDARD_PASS_REWARDS
    track_title = "Premium" if track_normalized == "premium" else "Standart"
    lines = [
        f"🪙 {CURRENT_PASS_SEASON['title']}",
        CURRENT_PASS_SEASON["subtitle"],
        "🔁 Premium действует только в рамках текущего сезона",
        "",
    ]
    for level in range(1, int(CURRENT_PASS_SEASON["max_level"]) + 1):
        reward = reward_map.get(level)
        reward_title = reward.get("title", "—") if reward else "—"
        lines.append(f"{level}. {reward_title}")
    return "\n".join(lines)
