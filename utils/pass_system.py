import json
import random
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import database_pg_async as aiosqlite
import pytz

from utils.business import BUSINESSES_CATALOG, PURCHASEABLE_BUSINESS_KEYS, add_business
from utils.case_system import CASE_DEFS, add_user_case
from utils.inventory import add_item
from utils.json_store import load as _json_store_load, save as _json_store_save
from utils.sqlite_utils import DB_WRITE_LOCK, connect_sqlite

DB_PATH = "database.db"
BALANCES_FILE = "balances.json"
MSK_TZ = pytz.timezone("Europe/Moscow")

CURRENT_PASS_SEASON = {
    "id": "banana_pass_fire_war",
    "title": "ADMIN PASS: Сквозь огонь войны",
    "subtitle": "Сезон, посвященный Победе над немецко-фашистскими захватчиками",
    "start": MSK_TZ.localize(datetime(2026, 5, 9, 0, 0, 0)),
    "end": MSK_TZ.localize(datetime(2026, 5, 29, 11, 0, 0)),
    "next_season_title": "Летняя пора",
    "premium_cost_bananas": 5000,
    "xp_per_level": 120,
    "max_level": 45,
    "daily_standard_quests": 10,
    "daily_premium_quests": 20,
    "daily_elite_quests": 5,
    "weekly_quests": 3,
}

SEASON_ITEM_POOL = []

PASS_QUEST_POOL: Dict[str, Dict] = {
    "claim_prize_1": {"event": "claim_prize", "target": 2, "xp": 20, "title": "Заберите /приз 2 раза", "elite": False},
    "claim_prize_2": {"event": "claim_prize", "target": 4, "xp": 32, "title": "Заберите /приз 4 раза", "elite": False},
    "claim_prize_3": {"event": "claim_prize", "target": 6, "xp": 50, "title": "Заберите /приз 6 раз", "elite": True},
    "open_case_1": {"event": "open_case", "target": 2, "xp": 22, "title": "Откройте 2 кейса", "elite": False},
    "open_case_2": {"event": "open_case", "target": 4, "xp": 35, "title": "Откройте 4 кейса", "elite": False},
    "open_case_4": {"event": "open_case", "target": 7, "xp": 55, "title": "Откройте 7 кейсов", "elite": True},
    "casino_play_2": {"event": "casino_play", "target": 4, "xp": 24, "title": "Сыграйте в /казино 4 раза", "elite": False},
    "casino_play_4": {"event": "casino_play", "target": 7, "xp": 38, "title": "Сыграйте в /казино 7 раз", "elite": False},
    "casino_play_7": {"event": "casino_play", "target": 12, "xp": 60, "title": "Сыграйте в /казино 12 раз", "elite": True},
    "duel_play_1": {"event": "duel_play", "target": 2, "xp": 24, "title": "Сыграйте 2 дуэли на 10.000.000₽", "elite": False},
    "duel_play_2": {"event": "duel_play", "target": 4, "xp": 38, "title": "Сыграйте 4 дуэли на 10.000.000₽", "elite": False},
    "duel_play_4": {"event": "duel_play", "target": 7, "xp": 58, "title": "Сыграйте 7 дуэлей на 10.000.000₽", "elite": True},
    "duel_create_1": {"event": "duel_play", "target": 2, "xp": 24, "title": "Сыграйте 2 дуэли на 10.000.000₽", "elite": False},
    "duel_create_2": {"event": "duel_play", "target": 4, "xp": 38, "title": "Сыграйте 4 дуэли на 10.000.000₽", "elite": False},
    "duel_create_4": {"event": "duel_play", "target": 7, "xp": 58, "title": "Сыграйте 7 дуэлей на 10.000.000₽", "elite": True},
    "business_upgrade_1": {"event": "business_upgrade", "target": 2, "xp": 28, "title": "Улучшите бизнес 2 раза", "elite": False},
    "business_upgrade_2": {"event": "business_upgrade", "target": 4, "xp": 42, "title": "Улучшите бизнес 4 раза", "elite": False},
    "business_upgrade_4": {"event": "business_upgrade", "target": 7, "xp": 65, "title": "Улучшите бизнес 7 раз", "elite": True},
    "collect_business_income_1": {"event": "collect_business_income", "target": 2, "xp": 26, "title": "Соберите доход с бизнесов 2 раза", "elite": False},
    "collect_business_income_3": {"event": "collect_business_income", "target": 5, "xp": 40, "title": "Соберите доход с бизнесов 5 раз", "elite": False},
    "collect_business_income_sum_50000000": {"event": "collect_business_income_money", "target": 150_000_000, "xp": 58, "title": "Соберите доход бизнеса на 150.000.000₽", "elite": False},
    "buy_business_1": {"event": "buy_business", "target": 2, "xp": 28, "title": "Купите 2 бизнеса", "elite": False},
    "buy_business_2": {"event": "buy_business", "target": 3, "xp": 46, "title": "Купите 3 бизнеса", "elite": True},
    "buy_bananas_1": {"event": "buy_bananas", "target": 2, "xp": 18, "title": "Купите коины 2 раза", "elite": False},
    "buy_bananas_3": {"event": "buy_bananas", "target": 5, "xp": 32, "title": "Купите коины 5 раз", "elite": False},
    "sell_bananas_1": {"event": "sell_bananas", "target": 2, "xp": 20, "title": "Продайте 100+ коинов 2 раза", "elite": False},
    "sell_bananas_3": {"event": "sell_bananas", "target": 5, "xp": 36, "title": "Продайте 100+ коинов 5 раз", "elite": False},
    "transfer_money_1": {"event": "transfer_money", "target": 3, "xp": 16, "title": "Сделайте 3 перевода", "elite": False},
    "transfer_money_4": {"event": "transfer_money", "target": 7, "xp": 34, "title": "Сделайте 7 переводов", "elite": False},
    "charity_1": {"event": "charity", "target": 2, "xp": 18, "title": "Пожертвуйте в /благо 2 раза", "elite": False},
    "charity_3": {"event": "charity", "target": 5, "xp": 42, "title": "Пожертвуйте в /благо 5 раз", "elite": True},
    "apply_item_1": {"event": "apply_item", "target": 2, "xp": 22, "title": "Примените 2 предмета", "elite": False},
    "apply_item_3": {"event": "apply_item", "target": 5, "xp": 45, "title": "Примените 5 предметов", "elite": True},
    "salvage_item_1": {"event": "salvage_item", "target": 2, "xp": 18, "title": "Распылите 2 предмета", "elite": False},
    "salvage_item_3": {"event": "salvage_item", "target": 5, "xp": 34, "title": "Распылите 5 предметов", "elite": False},
    "buy_vip_1": {"event": "buy_vip", "target": 1, "xp": 50, "title": "Купите VIP-статус", "elite": True},
}

WEEKLY_PASS_QUEST_POOL: Dict[str, Dict] = {
    "weekly_collect_business_income_5": {"event": "collect_business_income", "target": 8, "xp": 95, "title": "Соберите доход с бизнесов 8 раз"},
    "weekly_collect_business_income_sum_50000000": {"event": "collect_business_income_money", "target": 250_000_000, "xp": 110, "title": "Соберите доход с бизнесов на 250.000.000₽"},
    "weekly_collect_business_income_sum_100000000": {"event": "collect_business_income_money", "target": 500_000_000, "xp": 135, "title": "Соберите доход с бизнесов на 500.000.000₽"},
    "weekly_duel_play_5": {"event": "duel_play", "target": 8, "xp": 95, "title": "Сыграйте 8 дуэлей на 10.000.000₽"},
    "weekly_open_case_8": {"event": "open_case", "target": 14, "xp": 105, "title": "Откройте 14 кейсов"},
    "weekly_business_upgrade_6": {"event": "business_upgrade", "target": 10, "xp": 120, "title": "Улучшите бизнес 10 раз"},
}

POST_PASS_SPECIAL_QUEST_POOL: Dict[str, Dict] = {
    "special_claim_prize_1": {"event": "claim_prize", "target": 1, "title": "Заберите /приз 1 раз", "reward": {"type": "money", "amount": 250_000, "title": "250.000₽"}},
    "special_open_case_1": {"event": "open_case", "target": 1, "title": "Откройте 1 кейс", "reward": {"type": "bananas", "amount": 35, "title": "35 коинов"}},
    "special_casino_play_2": {"event": "casino_play", "target": 2, "title": "Сыграйте в /казино 2 раза", "reward": {"type": "money", "amount": 300_000, "title": "300.000₽"}},
    "special_duel_play_1": {"event": "duel_play", "target": 1, "title": "Сыграйте 1 дуэль", "reward": {"type": "case", "case_type": "homeless", "title": "Кейс Бомжа"}},
    "special_collect_business_income_1": {"event": "collect_business_income", "target": 1, "title": "Соберите доход с бизнеса 1 раз", "reward": {"type": "money", "amount": 350_000, "title": "350.000₽"}},
    "special_buy_bananas_1": {"event": "buy_bananas", "target": 1, "title": "Купите коины 1 раз", "reward": {"type": "bananas", "amount": 45, "title": "45 коинов"}},
    "special_sell_bananas_1": {"event": "sell_bananas", "target": 1, "title": "Продайте коины 1 раз", "reward": {"type": "money", "amount": 300_000, "title": "300.000₽"}},
    "special_transfer_money_2": {"event": "transfer_money", "target": 2, "title": "Сделайте 2 перевода", "reward": {"type": "bananas", "amount": 30, "title": "30 коинов"}},
    "special_charity_1": {"event": "charity", "target": 1, "title": "Пожертвуйте в /благо 1 раз", "reward": {"type": "money", "amount": 400_000, "title": "400.000₽"}},
    "special_apply_item_1": {"event": "apply_item", "target": 1, "title": "Примените 1 предмет", "reward": {"type": "case", "case_type": "standard", "title": "Стандартный кейс"}},
    "special_salvage_item_1": {"event": "salvage_item", "target": 1, "title": "Распылите 1 предмет", "reward": {"type": "bananas", "amount": 40, "title": "40 коинов"}},
    "special_buy_business_1": {"event": "buy_business", "target": 1, "title": "Купите 1 бизнес", "reward": {"type": "vip_days", "days": 1, "title": "VIP на 1 день"}},
}


def _generate_standard_rewards() -> Dict[int, Dict]:
    return {
        1: {"type": "vip_days", "days": 7, "title": "VIP на 7 дней"},
        2: {"type": "money", "amount": 1_000_000, "title": "1.000.000₽"},
        3: {"type": "bananas", "amount": 50, "title": "50 коинов"},
        4: {"type": "case", "case_type": "homeless", "title": "Кейс Бомжа"},
        5: {"type": "money", "amount": 1_500_000, "title": "1.500.000₽"},
        6: {"type": "item", "item_type": "season_collectible", "item_name": 'Фронтовая каска "Победа"', "item_value": 0, "title": 'Фронтовая каска "Победа"'},
        7: {"type": "bananas", "amount": 80, "title": "80 коинов"},
        8: {"type": "case", "case_type": "standard", "title": "Стандартный кейс"},
        9: {"type": "money", "amount": 2_000_000, "title": "2.000.000₽"},
        10: {"type": "vip_days", "days": 14, "title": "VIP на 14 дней"},
        11: {"type": "bananas", "amount": 120, "title": "120 коинов"},
        12: {"type": "money", "amount": 2_500_000, "title": "2.500.000₽"},
        13: {"type": "money", "amount": 2_500_000, "title": "2.500.000₽"},
        14: {"type": "case", "case_type": "standard", "title": "Стандартный кейс"},
        15: {"type": "bananas", "amount": 160, "title": "160 коинов"},
        16: {"type": "money", "amount": 3_000_000, "title": "3.000.000₽"},
        17: {"type": "item", "item_type": "season_collectible", "item_name": 'Полевая фляга "Май 45"', "item_value": 0, "title": 'Полевая фляга "Май 45"'},
        18: {"type": "case", "case_type": "standard", "title": "Стандартный кейс"},
        19: {"type": "money", "amount": 3_500_000, "title": "3.500.000₽"},
        20: {"type": "business", "business_key": "grand_espresso", "fallback_money": 2_500_000, "title": "Бизнес ветерана"},
        21: {"type": "bananas", "amount": 220, "title": "220 коинов"},
        22: {"type": "vip_days", "days": 14, "title": "VIP на 14 дней"},
        23: {"type": "money", "amount": 4_500_000, "title": "4.500.000₽"},
        24: {"type": "money", "amount": 5_000_000, "title": "5.000.000₽"},
        25: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        26: {"type": "bananas", "amount": 280, "title": "280 коинов"},
        27: {"type": "money", "amount": 5_500_000, "title": "5.500.000₽"},
        28: {"type": "item", "item_type": "season_collectible", "item_name": "Охотничья панама разведчика", "item_value": 0, "title": "Панама разведчика"},
        29: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        30: {"type": "vip_days", "days": 30, "title": "VIP на 30 дней"},
        31: {"type": "bananas", "amount": 350, "title": "350 коинов"},
        32: {"type": "money", "amount": 7_000_000, "title": "7.000.000₽"},
        33: {"type": "money", "amount": 7_500_000, "title": "7.500.000₽"},
        34: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        35: {"type": "money", "amount": 8_500_000, "title": "8.500.000₽"},
        36: {"type": "bananas", "amount": 450, "title": "450 коинов"},
        37: {"type": "item", "item_type": "season_collectible", "item_name": "Плащ-палатка командира", "item_value": 0, "title": "Плащ-палатка командира"},
        38: {"type": "money", "amount": 10_000_000, "title": "10.000.000₽"},
        39: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        40: {"type": "business", "business_key": "golden_croissant", "fallback_money": 5_000_000, "title": "Бизнес штаба"},
        41: {"type": "bananas", "amount": 600, "title": "600 коинов"},
        42: {"type": "vip_days", "days": 30, "title": "VIP на 30 дней"},
        43: {"type": "money", "amount": 12_500_000, "title": "12.500.000₽"},
        44: {"type": "money", "amount": 13_000_000, "title": "13.000.000₽"},
        45: {"type": "business", "business_key": "gourmania", "fallback_money": 9_000_000, "title": "Легендарный бизнес фронта"},
    }


def _generate_premium_rewards() -> Dict[int, Dict]:
    return {
        1: {"type": "money", "amount": 2_000_000, "title": "2.000.000₽"},
        2: {"type": "bananas", "amount": 120, "title": "120 коинов"},
        3: {"type": "vip_days", "days": 14, "title": "VIP на 14 дней"},
        4: {"type": "case", "case_type": "standard", "title": "Стандартный кейс"},
        5: {"type": "money", "amount": 3_500_000, "title": "3.500.000₽"},
        6: {"type": "money", "amount": 3_000_000, "title": "3.000.000₽"},
        7: {"type": "bananas", "amount": 180, "title": "180 коинов"},
        8: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        9: {"type": "money", "amount": 4_000_000, "title": "4.000.000₽"},
        10: {"type": "business", "business_key": "fashion_house", "fallback_money": 4_000_000, "title": "Элитный бизнес снабжения"},
        11: {"type": "vip_days", "days": 30, "title": "VIP на 30 дней"},
        12: {"type": "bananas", "amount": 240, "title": "240 коинов"},
        13: {"type": "item", "item_type": "season_collectible", "item_name": "Офицерский планшет Победы", "item_value": 0, "title": "Офицерский планшет"},
        14: {"type": "money", "amount": 5_500_000, "title": "5.500.000₽"},
        15: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        16: {"type": "bananas", "amount": 320, "title": "320 коинов"},
        17: {"type": "money", "amount": 7_500_000, "title": "7.500.000₽"},
        18: {"type": "money", "amount": 7_000_000, "title": "7.000.000₽"},
        19: {"type": "vip_days", "days": 30, "title": "VIP на 30 дней"},
        20: {"type": "business", "business_key": "gourmania", "fallback_money": 7_000_000, "title": "Бизнес командования"},
        21: {"type": "bananas", "amount": 420, "title": "420 коинов"},
        22: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        23: {"type": "money", "amount": 8_500_000, "title": "8.500.000₽"},
        24: {"type": "money", "amount": 9_000_000, "title": "9.000.000₽"},
        25: {"type": "bananas", "amount": 520, "title": "520 коинов"},
        26: {"type": "money", "amount": 10_000_000, "title": "10.000.000₽"},
        27: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        28: {"type": "money", "amount": 10_500_000, "title": "10.500.000₽"},
        29: {"type": "vip_days", "days": 30, "title": "VIP на 30 дней"},
        30: {"type": "business", "business_key": "global_market", "fallback_money": 10_000_000, "title": "Бизнес тыла"},
        31: {"type": "bananas", "amount": 700, "title": "700 коинов"},
        32: {"type": "money", "amount": 12_000_000, "title": "12.000.000₽"},
        33: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        34: {"type": "item", "item_type": "season_collectible", "item_name": "Парадная фуражка маршала", "item_value": 0, "title": "Парадная фуражка"},
        35: {"type": "money", "amount": 14_000_000, "title": "14.000.000₽"},
        36: {"type": "bananas", "amount": 900, "title": "900 коинов"},
        37: {"type": "vip_days", "days": 90, "title": "VIP на 90 дней"},
        38: {"type": "money", "amount": 14_500_000, "title": "14.500.000₽"},
        39: {"type": "case", "case_type": "special", "title": "Особый кейс"},
        40: {"type": "business", "business_key": "fuel_giant", "fallback_money": 15_000_000, "title": "Топливная сеть фронта"},
        41: {"type": "bananas", "amount": 1200, "title": "1.200 коинов"},
        42: {"type": "money", "amount": 18_000_000, "title": "18.000.000₽"},
        43: {"type": "item", "item_type": "season_collectible", "item_name": "Знамя Победы", "item_value": 0, "title": "Знамя Победы"},
        44: {"type": "case", "case_type": "victory_day", "title": "Кейс День Победы"},
        45: {"type": "business", "business_key": "gourmania", "fallback_money": 15_000_000, "title": "Легендарный бизнес"},
    }


STANDARD_PASS_REWARDS = _generate_standard_rewards()
PREMIUM_PASS_REWARDS = _generate_premium_rewards()


FIRE_WAR_PASS_SEASON = dict(CURRENT_PASS_SEASON)
FIRE_WAR_PASS_SEASON.update({
    "end": MSK_TZ.localize(datetime(2026, 5, 17, 15, 0, 0)),
    "next_season_title": "Летний переполох",
})
FIRE_WAR_ITEM_POOL = list(SEASON_ITEM_POOL)
FIRE_WAR_PASS_QUEST_POOL = dict(PASS_QUEST_POOL)
FIRE_WAR_WEEKLY_PASS_QUEST_POOL = dict(WEEKLY_PASS_QUEST_POOL)
FIRE_WAR_SPECIAL_QUEST_POOL = dict(POST_PASS_SPECIAL_QUEST_POOL)
FIRE_WAR_STANDARD_PASS_REWARDS = dict(STANDARD_PASS_REWARDS)
FIRE_WAR_PREMIUM_PASS_REWARDS = dict(PREMIUM_PASS_REWARDS)

SUMMER_PASS_SEASON = {
    "id": "banana_pass_summer_mayhem",
    "title": "ADMIN PASS: Летний переполох",
    "subtitle": "100 уровней летней охоты за кейсами, бизнес-бустами и редкими наградами",
    "start": MSK_TZ.localize(datetime(2026, 5, 17, 15, 0, 0)),
    "end": MSK_TZ.localize(datetime(2026, 7, 17, 15, 0, 0)),
    "next_season_title": "Жаркий август",
    "premium_cost_bananas": 50_000,
    "xp_per_level": 150,
    "max_level": 100,
    "daily_standard_quests": 10,
    "daily_premium_quests": 20,
    "daily_elite_quests": 5,
    "weekly_quests": 4,
}

SUMMER_ITEM_POOL = []

SUMMER_ITEM_LIBRARY = {}

SUMMER_PASS_QUEST_POOL: Dict[str, Dict] = {
    "claim_prize_4": {"event": "claim_prize", "target": 4, "xp": 16, "title": "Заберите /приз 4 раза", "elite": False},
    "claim_prize_8": {"event": "claim_prize", "target": 8, "xp": 28, "title": "Заберите /приз 8 раз", "elite": False},
    "open_case_3": {"event": "open_case", "target": 3, "xp": 18, "title": "Откройте 3 кейса", "elite": False},
    "open_case_6": {"event": "open_case", "target": 6, "xp": 30, "title": "Откройте 6 кейсов", "elite": False},
    "casino_play_5": {"event": "casino_play", "target": 5, "xp": 18, "title": "Сыграйте в /казино 5 раз", "elite": False},
    "casino_play_10": {"event": "casino_play", "target": 10, "xp": 30, "title": "Сыграйте в /казино 10 раз", "elite": False},
    "duel_play_3": {"event": "duel_play", "target": 3, "xp": 20, "title": "Сыграйте 3 дуэли на 10.000.000₽", "elite": False},
    "duel_play_6": {"event": "duel_play", "target": 6, "xp": 34, "title": "Сыграйте 6 дуэлей на 10.000.000₽", "elite": False},
    "business_upgrade_3": {"event": "business_upgrade", "target": 3, "xp": 22, "title": "Улучшите бизнес 3 раза", "elite": False},
    "business_upgrade_6": {"event": "business_upgrade", "target": 6, "xp": 36, "title": "Улучшите бизнес 6 раз", "elite": False},
    "collect_business_income_3": {"event": "collect_business_income", "target": 3, "xp": 20, "title": "Соберите доход с бизнесов 3 раза", "elite": False},
    "collect_business_income_6": {"event": "collect_business_income", "target": 6, "xp": 34, "title": "Соберите доход с бизнесов 6 раз", "elite": False},
    "collect_business_income_sum_250m": {"event": "collect_business_income_money", "target": 250_000_000, "xp": 38, "title": "Соберите доход бизнеса на 250.000.000₽", "elite": False},
    "buy_business_2": {"event": "buy_business", "target": 2, "xp": 26, "title": "Купите 2 бизнеса", "elite": False},
    "buy_bananas_3": {"event": "buy_bananas", "target": 3, "xp": 14, "title": "Купите коины 3 раза", "elite": False},
    "buy_bananas_6": {"event": "buy_bananas", "target": 6, "xp": 24, "title": "Купите коины 6 раз", "elite": False},
    "sell_bananas_3": {"event": "sell_bananas", "target": 3, "xp": 16, "title": "Продайте 100+ коинов 3 раза", "elite": False},
    "sell_bananas_6": {"event": "sell_bananas", "target": 6, "xp": 26, "title": "Продайте 100+ коинов 6 раз", "elite": False},
    "transfer_money_5": {"event": "transfer_money", "target": 5, "xp": 14, "title": "Сделайте 5 переводов", "elite": False},
    "transfer_money_10": {"event": "transfer_money", "target": 10, "xp": 26, "title": "Сделайте 10 переводов", "elite": False},
    "charity_2": {"event": "charity", "target": 2, "xp": 18, "title": "Пожертвуйте в /благо 2 раза", "elite": False},
    "charity_5": {"event": "charity", "target": 5, "xp": 30, "title": "Пожертвуйте в /благо 5 раз", "elite": False},
    "apply_item_2": {"event": "apply_item", "target": 2, "xp": 18, "title": "Примените 2 предмета", "elite": False},
    "apply_item_4": {"event": "apply_item", "target": 4, "xp": 28, "title": "Примените 4 предмета", "elite": False},
    "salvage_item_2": {"event": "salvage_item", "target": 2, "xp": 16, "title": "Распылите 2 предмета", "elite": False},
    "salvage_item_4": {"event": "salvage_item", "target": 4, "xp": 24, "title": "Распылите 4 предмета", "elite": False},
    "claim_prize_12": {"event": "claim_prize", "target": 12, "xp": 48, "title": "Заберите /приз 12 раз", "elite": True},
    "open_case_10": {"event": "open_case", "target": 10, "xp": 52, "title": "Откройте 10 кейсов", "elite": True},
    "casino_play_15": {"event": "casino_play", "target": 15, "xp": 54, "title": "Сыграйте в /казино 15 раз", "elite": True},
    "duel_play_8": {"event": "duel_play", "target": 8, "xp": 58, "title": "Сыграйте 8 дуэлей на 10.000.000₽", "elite": True},
    "business_upgrade_8": {"event": "business_upgrade", "target": 8, "xp": 60, "title": "Улучшите бизнес 8 раз", "elite": True},
    "collect_business_income_10": {"event": "collect_business_income", "target": 10, "xp": 56, "title": "Соберите доход с бизнесов 10 раз", "elite": True},
    "collect_business_income_sum_750m": {"event": "collect_business_income_money", "target": 750_000_000, "xp": 64, "title": "Соберите доход бизнеса на 750.000.000₽", "elite": True},
    "buy_business_4": {"event": "buy_business", "target": 4, "xp": 70, "title": "Купите 4 бизнеса", "elite": True},
    "apply_item_6": {"event": "apply_item", "target": 6, "xp": 52, "title": "Примените 6 предметов", "elite": True},
    "buy_vip_1": {"event": "buy_vip", "target": 1, "xp": 60, "title": "Купите VIP-статус", "elite": True},
}

SUMMER_WEEKLY_PASS_QUEST_POOL: Dict[str, Dict] = {
    "weekly_collect_income_12": {"event": "collect_business_income", "target": 12, "xp": 90, "title": "Соберите доход с бизнесов 12 раз"},
    "weekly_collect_income_25": {"event": "collect_business_income", "target": 25, "xp": 120, "title": "Соберите доход с бизнесов 25 раз"},
    "weekly_collect_income_sum_1500m": {"event": "collect_business_income_money", "target": 1_500_000_000, "xp": 110, "title": "Соберите доход бизнеса на 1.500.000.000₽"},
    "weekly_open_case_18": {"event": "open_case", "target": 18, "xp": 100, "title": "Откройте 18 кейсов"},
    "weekly_duel_play_12": {"event": "duel_play", "target": 12, "xp": 105, "title": "Сыграйте 12 дуэлей на 10.000.000₽"},
    "weekly_business_upgrade_14": {"event": "business_upgrade", "target": 14, "xp": 120, "title": "Улучшите бизнес 14 раз"},
    "weekly_apply_item_8": {"event": "apply_item", "target": 8, "xp": 95, "title": "Примените 8 предметов"},
    "weekly_buy_business_5": {"event": "buy_business", "target": 5, "xp": 130, "title": "Купите 5 бизнесов"},
}

SUMMER_POST_PASS_SPECIAL_QUEST_POOL: Dict[str, Dict] = {
    "special_claim_prize_2": {"event": "claim_prize", "target": 2, "title": "Заберите /приз 2 раза", "reward": {"type": "money", "amount": 500_000, "title": "500.000₽"}},
    "special_open_case_2": {"event": "open_case", "target": 2, "title": "Откройте 2 кейса", "reward": {"type": "bananas", "amount": 70, "title": "70 коинов"}},
    "special_casino_play_3": {"event": "casino_play", "target": 3, "title": "Сыграйте в /казино 3 раза", "reward": {"type": "money", "amount": 750_000, "title": "750.000₽"}},
    "special_duel_play_2": {"event": "duel_play", "target": 2, "title": "Сыграйте 2 дуэли", "reward": {"type": "case", "case_type": "summer_mayhem", "title": "Кейс Летний переполох"}},
    "special_collect_income_2": {"event": "collect_business_income", "target": 2, "title": "Соберите доход с бизнеса 2 раза", "reward": {"type": "money", "amount": 1_000_000, "title": "1.000.000₽"}},
    "special_buy_bananas_2": {"event": "buy_bananas", "target": 2, "title": "Купите коины 2 раза", "reward": {"type": "bananas", "amount": 100, "title": "100 коинов"}},
    "special_sell_bananas_2": {"event": "sell_bananas", "target": 2, "title": "Продайте коины 2 раза", "reward": {"type": "money", "amount": 900_000, "title": "900.000₽"}},
    "special_transfer_money_3": {"event": "transfer_money", "target": 3, "title": "Сделайте 3 перевода", "reward": {"type": "bananas", "amount": 60, "title": "60 коинов"}},
    "special_apply_item_2": {"event": "apply_item", "target": 2, "title": "Примените 2 предмета", "reward": {"type": "money", "amount": 100000, "title": "100.000₽"}},
    "special_buy_business_1": {"event": "buy_business", "target": 1, "title": "Купите 1 бизнес", "reward": {"type": "vip_days", "days": 3, "title": "VIP на 3 дня"}},
}


def _resolve_reward_case_title(case_type: str) -> str:
    return CASE_DEFS.get(case_type, {}).get("name", case_type)


def _money_reward(amount: int) -> Dict:
    return {"type": "money", "amount": int(amount), "title": f"{int(amount):,}₽".replace(",", ".")}


def _banana_reward(amount: int) -> Dict:
    return {"type": "bananas", "amount": int(amount), "title": f"{int(amount):,} коинов".replace(",", ".")}


def _vip_reward(days: int) -> Dict:
    return {"type": "vip_days", "days": int(days), "title": f"VIP на {int(days)} дн."}


def _case_reward(case_type: str) -> Dict:
    return {"type": "case", "case_type": case_type, "title": _resolve_reward_case_title(case_type)}


def _business_reward(business_key: str, fallback_money: int) -> Dict:
    business_name = BUSINESSES_CATALOG.get(business_key, {}).get("name", business_key)
    return {
        "type": "business",
        "business_key": business_key,
        "fallback_money": int(fallback_money),
        "title": business_name,
    }


def _summer_item_reward(item_key: str) -> Dict:
    item_meta = SUMMER_ITEM_LIBRARY[item_key]
    return {
        "type": "item",
        "item_type": item_meta["item_type"],
        "item_name": item_meta["item_name"],
        "item_value": int(item_meta["item_value"]),
        "title": item_meta["title"],
    }


def _generate_summer_standard_rewards() -> Dict[int, Dict]:
    rewards: Dict[int, Dict] = {}
    business_cycle = [
        (20, "golden_croissant", 3_000_000),
        (40, "fashion_house", 6_000_000),
        (60, "gourmania", 13_000_000),
        (80, "global_market", 20_000_000),
    ]
    business_map = {level: _business_reward(key, fallback) for level, key, fallback in business_cycle}

    for level in range(1, 101):
        phase = (level - 1) // 10
        slot = (level - 1) % 10
        if slot == 0:
            rewards[level] = _banana_reward(45 + phase * 20)
        elif slot == 1:
            rewards[level] = _money_reward(1_500_000 + phase * 1_250_000)
        elif slot == 2:
            rewards[level] = _money_reward(2_250_000 + phase * 1_500_000)
        elif slot == 3:
            rewards[level] = _case_reward("homeless" if level < 15 else "standard")
        elif slot == 4:
            rewards[level] = _vip_reward(7 if level < 40 else 14)
        elif slot == 5:
            rewards[level] = _banana_reward(70 + phase * 25)
        elif slot == 6:
            rewards[level] = _money_reward(3_000_000 + phase * 1_750_000)
        elif slot == 7:
            rewards[level] = _case_reward("standard")
        elif slot == 8:
            rewards[level] = _banana_reward(95 + phase * 30)
        else:
            rewards[level] = _money_reward(4_250_000 + phase * 2_250_000)

    rewards.update({
        12: _money_reward(3_500_000),
        18: _money_reward(4_000_000),
        24: _case_reward("summer_mayhem"),
        30: business_map[20],
        36: _money_reward(5_500_000),
        45: _case_reward("summer_mayhem"),
        50: business_map[40],
        54: _money_reward(6_500_000),
        63: _money_reward(8_000_000),
        70: business_map[60],
        72: _case_reward("summer_mayhem"),
        81: _money_reward(10_000_000),
        90: business_map[80],
        96: _money_reward(12_000_000),
        100: _case_reward("summer_mayhem"),
    })
    return rewards


def _generate_summer_premium_rewards() -> Dict[int, Dict]:
    rewards: Dict[int, Dict] = {}
    business_cycle = [
        (10, "fashion_house", 6_000_000),
        (25, "gourmania", 13_000_000),
        (40, "global_market", 20_000_000),
        (55, "fuel_giant", 75_000_000),
        (70, "neboskreb", 200_000_000),
        (85, "imax_empire", 300_000_000),
    ]
    business_map = {level: _business_reward(key, fallback) for level, key, fallback in business_cycle}

    for level in range(1, 101):
        phase = (level - 1) // 10
        slot = (level - 1) % 10
        if slot == 0:
            rewards[level] = _money_reward(3_500_000 + phase * 2_500_000)
        elif slot == 1:
            rewards[level] = _banana_reward(120 + phase * 45)
        elif slot == 2:
            rewards[level] = _vip_reward(14 if level < 50 else 30)
        elif slot == 3:
            rewards[level] = _case_reward("standard" if level < 20 else "summer_mayhem")
        elif slot == 4:
            rewards[level] = _money_reward(5_500_000 + phase * 3_000_000)
        elif slot == 5:
            rewards[level] = _banana_reward(180 + phase * 55)
        elif slot == 6:
            rewards[level] = _case_reward("special" if level < 40 else "summer_mayhem")
        elif slot == 7:
            rewards[level] = _money_reward(7_500_000 + phase * 3_500_000)
        elif slot == 8:
            rewards[level] = _banana_reward(240 + phase * 65)
        else:
            rewards[level] = _vip_reward(30 if level < 60 else 45)

    rewards.update({
        5: _money_reward(6_000_000),
        10: business_map[10],
        15: _money_reward(9_000_000),
        20: _case_reward("summer_mayhem"),
        25: business_map[25],
        28: _money_reward(11_000_000),
        35: _money_reward(13_000_000),
        40: business_map[40],
        44: _case_reward("summer_mayhem"),
        50: _money_reward(16_000_000),
        55: business_map[55],
        60: _case_reward("summer_mayhem"),
        66: _money_reward(19_000_000),
        70: business_map[70],
        75: _money_reward(21_000_000),
        80: _case_reward("summer_mayhem"),
        85: business_map[85],
        90: _money_reward(24_000_000),
        95: _money_reward(26_000_000),
        100: _case_reward("summer_mayhem"),
    })
    return rewards


SUMMER_STANDARD_PASS_REWARDS = _generate_summer_standard_rewards()
SUMMER_PREMIUM_PASS_REWARDS = _generate_summer_premium_rewards()

BLACK_CITY_PASS_SEASON = {
    "id": "banana_pass_black_city",
    "title": "ADMIN PASS: Чёрный город",
    "subtitle": "100 уровней пути от простого водителя до владельца целой области",
    "start": MSK_TZ.localize(datetime(2026, 7, 17, 15, 0, 0)),
    "end": MSK_TZ.localize(datetime(2026, 9, 17, 15, 0, 0)),
    "next_season_title": "Новый сезон",
    "premium_cost_bananas": 75_000,
    "xp_per_level": 160,
    "max_level": 100,
    "daily_standard_quests": 10,
    "daily_premium_quests": 20,
    "daily_elite_quests": 5,
    "weekly_quests": 4,
}

BLACK_CITY_ITEM_LIBRARY = {}
BLACK_CITY_ITEM_POOL = []


def _black_city_item_reward(item_key: str) -> Dict:
    item_meta = BLACK_CITY_ITEM_LIBRARY[item_key]
    return {
        "type": "item",
        "item_type": item_meta["item_type"],
        "item_name": item_meta["item_name"],
        "item_value": int(item_meta["item_value"]),
        "title": item_meta["title"],
    }


def _generate_black_city_standard_rewards() -> Dict[int, Dict]:
    rewards: Dict[int, Dict] = {}
    business_levels = {
        20: _business_reward("fashion_house", 6_000_000),
        40: _business_reward("gourmania", 13_000_000),
        70: _business_reward("global_market", 20_000_000),
        90: _business_reward("fuel_giant", 75_000_000),
    }
    for level in range(1, 101):
        phase = (level - 1) // 10
        slot = (level - 1) % 10
        if slot == 0:
            rewards[level] = _banana_reward(70 + phase * 30)
        elif slot == 1:
            rewards[level] = _money_reward(2_500_000 + phase * 2_000_000)
        elif slot == 2:
            rewards[level] = _money_reward(4_000_000 + phase * 2_500_000)
        elif slot == 3:
            rewards[level] = _case_reward("standard")
        elif slot == 4:
            rewards[level] = _vip_reward(7 if level < 50 else 14)
        elif slot == 5:
            rewards[level] = _banana_reward(110 + phase * 35)
        elif slot == 6:
            rewards[level] = _money_reward(6_000_000 + phase * 3_000_000)
        elif slot == 7:
            rewards[level] = _case_reward("standard" if level < 40 else "special")
        elif slot == 8:
            rewards[level] = _banana_reward(150 + phase * 45)
        else:
            rewards[level] = _money_reward(8_000_000 + phase * 4_000_000)
    rewards.update({
        10: _case_reward("standard"),
        15: _money_reward(5_000_000),
        20: business_levels[20],
        25: _case_reward("black_point"),
        35: _money_reward(9_000_000),
        40: business_levels[40],
        50: _case_reward("black_point"),
        60: _money_reward(12_000_000),
        70: business_levels[70],
        75: _case_reward("black_point"),
        85: _money_reward(16_000_000),
        90: business_levels[90],
        95: _money_reward(18_000_000),
        99: _case_reward("black_point"),
        100: _money_reward(20_000_000),
    })
    return rewards


def _generate_black_city_premium_rewards() -> Dict[int, Dict]:
    rewards: Dict[int, Dict] = {}
    business_levels = {
        10: _business_reward("fashion_house", 6_000_000),
        25: _business_reward("gourmania", 13_000_000),
        40: _business_reward("global_market", 20_000_000),
        55: _business_reward("fuel_giant", 75_000_000),
        70: _business_reward("neboskreb", 200_000_000),
        90: _business_reward("imax_empire", 300_000_000),
    }
    for level in range(1, 101):
        phase = (level - 1) // 10
        slot = (level - 1) % 10
        if slot == 0:
            rewards[level] = _money_reward(5_000_000 + phase * 4_000_000)
        elif slot == 1:
            rewards[level] = _banana_reward(180 + phase * 65)
        elif slot == 2:
            rewards[level] = _vip_reward(14 if level < 50 else 30)
        elif slot == 3:
            rewards[level] = _case_reward("special" if level < 30 else "black_point")
        elif slot == 4:
            rewards[level] = _money_reward(8_000_000 + phase * 5_000_000)
        elif slot == 5:
            rewards[level] = _banana_reward(260 + phase * 80)
        elif slot == 6:
            rewards[level] = _case_reward("special")
        elif slot == 7:
            rewards[level] = _money_reward(12_000_000 + phase * 6_000_000)
        elif slot == 8:
            rewards[level] = _banana_reward(350 + phase * 100)
        else:
            rewards[level] = _vip_reward(30 if level < 60 else 45)
    rewards.update({
        5: _money_reward(10_000_000),
        10: business_levels[10],
        15: _case_reward("black_point"),
        20: _money_reward(13_000_000),
        25: business_levels[25],
        30: _case_reward("black_point"),
        40: _money_reward(18_000_000),
        45: _case_reward("black_point"),
        50: _money_reward(21_000_000),
        55: business_levels[55],
        60: _case_reward("black_point"),
        65: _money_reward(24_000_000),
        70: business_levels[70],
        75: _money_reward(27_000_000),
        80: _case_reward("black_point"),
        85: _money_reward(30_000_000),
        90: business_levels[90],
        95: _case_reward("black_point"),
        98: _vip_reward(90),
        99: _case_reward("black_point"),
        100: _money_reward(35_000_000),
    })
    return rewards


BLACK_CITY_PASS_QUEST_POOL = dict(SUMMER_PASS_QUEST_POOL)
BLACK_CITY_WEEKLY_PASS_QUEST_POOL = dict(SUMMER_WEEKLY_PASS_QUEST_POOL)
BLACK_CITY_POST_PASS_SPECIAL_QUEST_POOL: Dict[str, Dict] = {
    "black_special_claim_prize": {"event": "claim_prize", "target": 3, "title": "Заберите /приз 3 раза", "reward": {"type": "money", "amount": 2_500_000, "title": "2.500.000₽"}},
    "black_special_open_case": {"event": "open_case", "target": 3, "title": "Откройте 3 кейса", "reward": {"type": "case", "case_type": "black_point", "title": "Кейс BLACK POINT"}},
    "black_special_collect_income": {"event": "collect_business_income", "target": 3, "title": "Соберите доход с бизнеса 3 раза", "reward": {"type": "bananas", "amount": 150, "title": "150 коинов"}},
    "black_special_apply_item": {"event": "apply_item", "target": 2, "title": "Примените 2 предмета", "reward": {"type": "money", "amount": 250000, "title": "250.000₽"}},
    "black_special_buy_business": {"event": "buy_business", "target": 1, "title": "Купите 1 бизнес", "reward": {"type": "vip_days", "days": 5, "title": "VIP на 5 дней"}},
    "black_special_upgrade": {"event": "business_upgrade", "target": 4, "title": "Улучшите бизнес 4 раза", "reward": {"type": "bananas", "amount": 250, "title": "250 коинов"}},
}

BLACK_CITY_STANDARD_PASS_REWARDS = _generate_black_city_standard_rewards()
BLACK_CITY_PREMIUM_PASS_REWARDS = _generate_black_city_premium_rewards()

PASS_SEASONS = [FIRE_WAR_PASS_SEASON, SUMMER_PASS_SEASON, BLACK_CITY_PASS_SEASON]
PASS_ITEM_POOLS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_ITEM_POOL,
    SUMMER_PASS_SEASON["id"]: SUMMER_ITEM_POOL,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_ITEM_POOL,
}
PASS_QUEST_POOLS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_PASS_QUEST_POOL,
    SUMMER_PASS_SEASON["id"]: SUMMER_PASS_QUEST_POOL,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_PASS_QUEST_POOL,
}
PASS_WEEKLY_QUEST_POOLS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_WEEKLY_PASS_QUEST_POOL,
    SUMMER_PASS_SEASON["id"]: SUMMER_WEEKLY_PASS_QUEST_POOL,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_WEEKLY_PASS_QUEST_POOL,
}
PASS_SPECIAL_QUEST_POOLS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_SPECIAL_QUEST_POOL,
    SUMMER_PASS_SEASON["id"]: SUMMER_POST_PASS_SPECIAL_QUEST_POOL,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_POST_PASS_SPECIAL_QUEST_POOL,
}
PASS_STANDARD_REWARD_MAPS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_STANDARD_PASS_REWARDS,
    SUMMER_PASS_SEASON["id"]: SUMMER_STANDARD_PASS_REWARDS,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_STANDARD_PASS_REWARDS,
}
PASS_PREMIUM_REWARD_MAPS = {
    FIRE_WAR_PASS_SEASON["id"]: FIRE_WAR_PREMIUM_PASS_REWARDS,
    SUMMER_PASS_SEASON["id"]: SUMMER_PREMIUM_PASS_REWARDS,
    BLACK_CITY_PASS_SEASON["id"]: BLACK_CITY_PREMIUM_PASS_REWARDS,
}

PASS_QUEST_XP_BOOST_END = MSK_TZ.localize(datetime(2026, 5, 31, 23, 59, 59))
PASS_QUEST_XP_TEMP_MULTIPLIER = 3
PASS_QUEST_XP_DEFAULT_MULTIPLIER = 2


def _current_pass_quest_xp_multiplier(now: Optional[datetime] = None) -> int:
    current_time = now or datetime.now(MSK_TZ)
    if current_time <= PASS_QUEST_XP_BOOST_END:
        return PASS_QUEST_XP_TEMP_MULTIPLIER
    return PASS_QUEST_XP_DEFAULT_MULTIPLIER


def _with_pass_quest_xp_multiplier(pool: Dict[str, Dict]) -> Dict[str, Dict]:
    multiplier = _current_pass_quest_xp_multiplier()
    boosted_pool: Dict[str, Dict] = {}
    for quest_id, quest in pool.items():
        boosted_quest = dict(quest)
        if "xp" in boosted_quest:
            boosted_quest["xp"] = int(boosted_quest["xp"]) * multiplier
        boosted_pool[quest_id] = boosted_quest
    return boosted_pool


class _DynamicConfigDict(dict):
    def __init__(self, resolver: Callable[[], Dict]):
        self._resolver = resolver
        super().__init__(resolver() or {})

    def _refresh(self) -> None:
        data = self._resolver() or {}
        super().clear()
        super().update(data)

    def __getitem__(self, key):
        self._refresh()
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._refresh()
        return super().get(key, default)

    def items(self):
        self._refresh()
        return super().items()

    def keys(self):
        self._refresh()
        return super().keys()

    def values(self):
        self._refresh()
        return super().values()

    def copy(self):
        self._refresh()
        return dict(self)

    def __contains__(self, key):
        self._refresh()
        return super().__contains__(key)

    def __iter__(self):
        self._refresh()
        return super().__iter__()

    def __len__(self):
        self._refresh()
        return super().__len__()


def _resolve_current_pass_season(now: Optional[datetime] = None) -> Dict:
    current_time = now or datetime.now(MSK_TZ)
    active_seasons = [season for season in PASS_SEASONS if season["start"] <= current_time <= season["end"]]
    if active_seasons:
        return dict(max(active_seasons, key=lambda season: season["start"]))
    upcoming = [season for season in PASS_SEASONS if current_time < season["start"]]
    if upcoming:
        return dict(min(upcoming, key=lambda season: season["start"]))
    return dict(max(PASS_SEASONS, key=lambda season: season["end"]))


def _current_pass_season_id() -> str:
    return str(CURRENT_PASS_SEASON["id"])


def _resolve_season_item_pool() -> Dict:
    return {str(index): value for index, value in enumerate(PASS_ITEM_POOLS.get(_current_pass_season_id(), []), start=1)}


def _resolve_pass_quest_pool() -> Dict:
    return _with_pass_quest_xp_multiplier(PASS_QUEST_POOLS.get(_current_pass_season_id(), {}))


def _resolve_weekly_pass_quest_pool() -> Dict:
    return _with_pass_quest_xp_multiplier(PASS_WEEKLY_QUEST_POOLS.get(_current_pass_season_id(), {}))


def _resolve_special_pass_quest_pool() -> Dict:
    return dict(PASS_SPECIAL_QUEST_POOLS.get(_current_pass_season_id(), {}))


def _resolve_standard_reward_map() -> Dict:
    return dict(PASS_STANDARD_REWARD_MAPS.get(_current_pass_season_id(), {}))


def _resolve_premium_reward_map() -> Dict:
    return dict(PASS_PREMIUM_REWARD_MAPS.get(_current_pass_season_id(), {}))


CURRENT_PASS_SEASON = _DynamicConfigDict(_resolve_current_pass_season)
SEASON_ITEM_POOL = _DynamicConfigDict(_resolve_season_item_pool)
PASS_QUEST_POOL = _DynamicConfigDict(_resolve_pass_quest_pool)
WEEKLY_PASS_QUEST_POOL = _DynamicConfigDict(_resolve_weekly_pass_quest_pool)
POST_PASS_SPECIAL_QUEST_POOL = _DynamicConfigDict(_resolve_special_pass_quest_pool)
STANDARD_PASS_REWARDS = _DynamicConfigDict(_resolve_standard_reward_map)
PREMIUM_PASS_REWARDS = _DynamicConfigDict(_resolve_premium_reward_map)


def _now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def _today_str() -> str:
    return _now_msk().date().isoformat()


def _week_key() -> str:
    now = _now_msk().date()
    iso_year, iso_week, _iso_weekday = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _season_end_left_text() -> str:
    delta = CURRENT_PASS_SEASON["end"] - _now_msk()
    if delta.total_seconds() <= 0:
        return "сезон завершён"
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    return f"{delta.days}д. {hours}ч. {minutes}м."


def is_pass_active() -> bool:
    now = _now_msk()
    return CURRENT_PASS_SEASON["start"] <= now <= CURRENT_PASS_SEASON["end"]


def load_balances_data() -> Dict:
    # Балансы хранятся во внешней PostgreSQL (таблица json_store).
    data = _json_store_load(BALANCES_FILE, default=None)
    if not isinstance(data, dict):
        return {}
    return data


def save_balances_data(data: Dict) -> None:
    _json_store_save(BALANCES_FILE, data)


def ensure_balance_record(balances: Dict, user_id: int) -> Dict:
    uid = str(user_id)
    if uid not in balances:
        balances[uid] = {
            "wallet": 0,
            "bank": 0,
            "won_total": 0,
            "lost_total": 0,
            "sent_total": 0,
            "received_total": 0,
            "bananas": 0,
            "business_income_today": 0,
            "vip_until": None,
        }
    balances[uid].setdefault("bananas", 0)
    balances[uid].setdefault("vip_until", None)
    return balances[uid]


async def init_pass_schema() -> None:
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS banana_pass_users (
                user_id INTEGER NOT NULL,
                season_id TEXT NOT NULL,
                premium_active INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, season_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS banana_pass_claims (
                user_id INTEGER NOT NULL,
                season_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                track TEXT NOT NULL,
                claimed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, season_id, level, track)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS banana_pass_daily_quests (
                user_id INTEGER NOT NULL,
                season_id TEXT NOT NULL,
                quest_date TEXT NOT NULL,
                slot INTEGER NOT NULL,
                quest_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL,
                xp_reward INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0,
                is_elite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, season_id, quest_date, slot)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS banana_pass_weekly_quests (
                user_id INTEGER NOT NULL,
                season_id TEXT NOT NULL,
                week_key TEXT NOT NULL,
                slot INTEGER NOT NULL,
                quest_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL,
                xp_reward INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, season_id, week_key, slot)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS banana_pass_special_quests (
                user_id INTEGER NOT NULL,
                season_id TEXT NOT NULL,
                quest_date TEXT NOT NULL,
                slot INTEGER NOT NULL,
                quest_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL DEFAULT 1,
                completed INTEGER NOT NULL DEFAULT 0,
                rewarded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, season_id, quest_date, slot)
            )
            """
        )
        result = await (await db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'banana_pass_daily_quests'"
        )).fetchall()
        columns = {row[0] for row in result} if result else set()
        if "is_elite" not in columns:
            await db.execute("ALTER TABLE banana_pass_daily_quests ADD COLUMN is_elite INTEGER NOT NULL DEFAULT 0")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_banana_pass_users_season ON banana_pass_users(season_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_banana_pass_daily_date ON banana_pass_daily_quests(user_id, season_id, quest_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_banana_pass_weekly_date ON banana_pass_weekly_quests(user_id, season_id, week_key)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_banana_pass_special_date ON banana_pass_special_quests(user_id, season_id, quest_date)")
        await db.commit()


async def ensure_pass_user(user_id: int, db=None) -> Dict:
    season_id = CURRENT_PASS_SEASON["id"]
    if db is None:
        async with DB_WRITE_LOCK:
            db = await connect_sqlite()
            try:
                return await _ensure_pass_user_impl(db, user_id, season_id)
            finally:
                await db.close()
    else:
        return await _ensure_pass_user_impl(db, user_id, season_id)

async def _ensure_pass_user_impl(db, user_id, season_id):

    await db.execute(
            """
            INSERT INTO banana_pass_users (user_id, season_id, premium_active, xp, updated_at)
            VALUES (?, ?, 0, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, season_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, season_id),
        )
    await db.commit()
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT user_id, season_id, premium_active, xp FROM banana_pass_users WHERE user_id = ? AND season_id = ?",
        (user_id, season_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else {"user_id": user_id, "season_id": season_id, "premium_active": 0, "xp": 0}


def _split_quest_pool() -> Tuple[List[str], List[str]]:
    elite = [quest_id for quest_id, quest in PASS_QUEST_POOL.items() if quest.get("elite")]
    regular = [quest_id for quest_id, quest in PASS_QUEST_POOL.items() if not quest.get("elite")]
    return regular, elite


def _weekly_quest_ids() -> List[str]:
    return list(WEEKLY_PASS_QUEST_POOL.keys())


def _daily_quest_row_matches_meta(row: Dict) -> bool:
    quest = PASS_QUEST_POOL.get(str(row.get("quest_id")))
    if not quest:
        return False
    return (
        int(row.get("target", 0) or 0) == int(quest["target"])
        and int(row.get("xp_reward", 0) or 0) == int(quest["xp"])
        and int(row.get("is_elite", 0) or 0) == (1 if quest.get("elite") else 0)
    )


def _weekly_quest_row_matches_meta(row: Dict) -> bool:
    quest = WEEKLY_PASS_QUEST_POOL.get(str(row.get("quest_id")))
    if not quest:
        return False
    return (
        int(row.get("target", 0) or 0) == int(quest["target"])
        and int(row.get("xp_reward", 0) or 0) == int(quest["xp"])
    )


async def _sync_daily_quest_xp_rewards(db: aiosqlite.Connection, rows: List[Dict]) -> List[Dict]:
    changed = False
    for row in rows:
        quest = PASS_QUEST_POOL.get(str(row.get("quest_id")))
        if not quest:
            continue
        expected_xp = int(quest["xp"])
        if int(row.get("xp_reward", 0) or 0) == expected_xp:
            continue
        row["xp_reward"] = expected_xp
        changed = True
        await db.execute(
            """
            UPDATE banana_pass_daily_quests
            SET xp_reward = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND season_id = ? AND quest_date = ? AND slot = ?
            """,
            (
                expected_xp,
                int(row["user_id"]),
                str(row["season_id"]),
                str(row["quest_date"]),
                int(row["slot"]),
            ),
        )
    if changed:
        await db.commit()
    return rows


async def _sync_weekly_quest_xp_rewards(db: aiosqlite.Connection, rows: List[Dict]) -> List[Dict]:
    changed = False
    for row in rows:
        quest = WEEKLY_PASS_QUEST_POOL.get(str(row.get("quest_id")))
        if not quest:
            continue
        expected_xp = int(quest["xp"])
        if int(row.get("xp_reward", 0) or 0) == expected_xp:
            continue
        row["xp_reward"] = expected_xp
        changed = True
        await db.execute(
            """
            UPDATE banana_pass_weekly_quests
            SET xp_reward = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND season_id = ? AND week_key = ? AND slot = ?
            """,
            (
                expected_xp,
                int(row["user_id"]),
                str(row["season_id"]),
                str(row["week_key"]),
                int(row["slot"]),
            ),
        )
    if changed:
        await db.commit()
    return rows


async def ensure_daily_quests(user_id: int) -> List[Dict]:
    season_id = CURRENT_PASS_SEASON["id"]
    quest_date = _today_str()

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            user_data = await ensure_pass_user(user_id, db)
            is_premium = bool(int(user_data.get("premium_active", 0) or 0))
            regular_count = CURRENT_PASS_SEASON["daily_premium_quests"] if is_premium else CURRENT_PASS_SEASON["daily_standard_quests"]
            elite_count = CURRENT_PASS_SEASON["daily_elite_quests"]
            regular_pool, elite_pool = _split_quest_pool()

            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT user_id, season_id, quest_date, slot, quest_id, progress, target, xp_reward, completed, notified, is_elite
                FROM banana_pass_daily_quests
                WHERE user_id = ? AND season_id = ? AND quest_date = ?
                ORDER BY slot
                """,
                (user_id, season_id, quest_date),
            )
            rows = [dict(row) for row in await cur.fetchall()]
            rows = await _sync_daily_quest_xp_rewards(db, rows)
            expected_count = regular_count + elite_count
            if len(rows) == expected_count and all(_daily_quest_row_matches_meta(row) for row in rows):
                return rows
            if rows:
                await db.execute(
                    "DELETE FROM banana_pass_daily_quests WHERE user_id = ? AND season_id = ? AND quest_date = ?",
                    (user_id, season_id, quest_date),
                )

            rng = random.Random(f"{season_id}:{user_id}:{quest_date}:{'premium' if is_premium else 'standard'}")
            chosen_regular = rng.sample(regular_pool, k=min(regular_count, len(regular_pool)))
            chosen_elite = rng.sample(elite_pool, k=min(elite_count, len(elite_pool)))
            slot = 1
            for quest_id in chosen_regular:
                quest = PASS_QUEST_POOL[quest_id]
                await db.execute(
                    """
                    INSERT INTO banana_pass_daily_quests
                    (user_id, season_id, quest_date, slot, quest_id, progress, target, xp_reward, completed, notified, is_elite, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, 0, 0, CURRENT_TIMESTAMP)
                    """,
                    (user_id, season_id, quest_date, slot, quest_id, int(quest["target"]), int(quest["xp"])),
                )
                slot += 1
            for quest_id in chosen_elite:
                quest = PASS_QUEST_POOL[quest_id]
                await db.execute(
                    """
                    INSERT INTO banana_pass_daily_quests
                    (user_id, season_id, quest_date, slot, quest_id, progress, target, xp_reward, completed, notified, is_elite, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, 0, 1, CURRENT_TIMESTAMP)
                    """,
                    (user_id, season_id, quest_date, slot, quest_id, int(quest["target"]), int(quest["xp"])),
                )
                slot += 1
            await db.commit()
            cur = await db.execute(
                """
                SELECT user_id, season_id, quest_date, slot, quest_id, progress, target, xp_reward, completed, notified, is_elite
                FROM banana_pass_daily_quests
                WHERE user_id = ? AND season_id = ? AND quest_date = ?
                ORDER BY slot
                """,
                (user_id, season_id, quest_date),
            )
            return [dict(row) for row in await cur.fetchall()]
        except Exception as e:
            print(f"[PASS DAILY] Error for user {user_id}: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            raise
        finally:
            await db.close()

async def ensure_weekly_quests(user_id: int) -> List[Dict]:
    season_id = CURRENT_PASS_SEASON["id"]
    week_key = _week_key()
    weekly_count = int(CURRENT_PASS_SEASON.get("weekly_quests", 0) or 0)
    weekly_pool = _weekly_quest_ids()

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT user_id, season_id, week_key, slot, quest_id, progress, target, xp_reward, completed, notified
                FROM banana_pass_weekly_quests
                WHERE user_id = ? AND season_id = ? AND week_key = ?
                ORDER BY slot
                """,
                (user_id, season_id, week_key),
            )
            rows = [dict(row) for row in await cur.fetchall()]
            rows = await _sync_weekly_quest_xp_rewards(db, rows)
            expected_count = min(weekly_count, len(weekly_pool))
            if len(rows) == expected_count and all(_weekly_quest_row_matches_meta(row) for row in rows):
                return rows
            if rows:
                await db.execute(
                    "DELETE FROM banana_pass_weekly_quests WHERE user_id = ? AND season_id = ? AND week_key = ?",
                    (user_id, season_id, week_key),
                )

            rng = random.Random(f"{season_id}:{user_id}:{week_key}:weekly")
            chosen_weekly = rng.sample(weekly_pool, k=expected_count)
            slot = 1
            for quest_id in chosen_weekly:
                quest = WEEKLY_PASS_QUEST_POOL[quest_id]
                await db.execute(
                    """
                    INSERT INTO banana_pass_weekly_quests
                    (user_id, season_id, week_key, slot, quest_id, progress, target, xp_reward, completed, notified, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, 0, CURRENT_TIMESTAMP)
                    """,
                    (user_id, season_id, week_key, slot, quest_id, int(quest["target"]), int(quest["xp"])),
                )
                slot += 1
            await db.commit()
            cur = await db.execute(
                """
                SELECT user_id, season_id, week_key, slot, quest_id, progress, target, xp_reward, completed, notified
                FROM banana_pass_weekly_quests
                WHERE user_id = ? AND season_id = ? AND week_key = ?
                ORDER BY slot
                """,
                (user_id, season_id, week_key),
            )
            return [dict(row) for row in await cur.fetchall()]
        except Exception as e:
            print(f"[PASS WEEKLY] Error for user {user_id}: {type(e).__name__}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            raise

        finally:
            await db.close()


def get_level_from_xp(xp: int) -> int:
    xp_per_level = int(CURRENT_PASS_SEASON["xp_per_level"])
    max_level = int(CURRENT_PASS_SEASON["max_level"])
    return max(1, min(max_level, xp // xp_per_level + 1))


def get_unlocked_level_count(xp: int) -> int:
    xp_per_level = int(CURRENT_PASS_SEASON["xp_per_level"])
    max_level = int(CURRENT_PASS_SEASON["max_level"])
    return max(0, min(max_level, xp // xp_per_level))


def is_post_pass_complete(xp: int) -> bool:
    xp_per_level = int(CURRENT_PASS_SEASON["xp_per_level"])
    max_level = int(CURRENT_PASS_SEASON["max_level"])
    return int(xp) >= max_level * xp_per_level


def get_reward_track(track: str) -> Dict[int, Dict]:
    return PREMIUM_PASS_REWARDS if track == "premium" else STANDARD_PASS_REWARDS


async def get_claimed_rewards_map(user_id: int) -> Dict[Tuple[int, str], bool]:
    season_id = CURRENT_PASS_SEASON["id"]
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        cur = await db.execute(
            "SELECT level, track FROM banana_pass_claims WHERE user_id = ? AND season_id = ?",
            (user_id, season_id),
        )
        rows = await cur.fetchall()
    return {(int(level), str(track)): True for level, track in rows}


async def ensure_special_quests(user_id: int) -> List[Dict]:
    season_id = CURRENT_PASS_SEASON["id"]
    quest_date = _today_str()
    expected_count = 5
    quest_pool_ids = list(POST_PASS_SPECIAL_QUEST_POOL.keys())

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT user_id, season_id, quest_date, slot, quest_id, progress, target, completed, rewarded
                FROM banana_pass_special_quests
                WHERE user_id = ? AND season_id = ? AND quest_date = ?
                ORDER BY slot
                """,
                (user_id, season_id, quest_date),
            )
            rows = [dict(row) for row in await cur.fetchall()]
            if len(rows) == expected_count and all(str(row.get("quest_id")) in POST_PASS_SPECIAL_QUEST_POOL for row in rows):
                return rows
            if rows:
                await db.execute(
                    "DELETE FROM banana_pass_special_quests WHERE user_id = ? AND season_id = ? AND quest_date = ?",
                    (user_id, season_id, quest_date),
                )
            rng = random.Random(f"{season_id}:{user_id}:{quest_date}:special")
            chosen = rng.sample(quest_pool_ids, k=min(expected_count, len(quest_pool_ids)))
            slot = 1
            for quest_id in chosen:
                quest = POST_PASS_SPECIAL_QUEST_POOL[quest_id]
                await db.execute(
                    """
                    INSERT INTO banana_pass_special_quests
                    (user_id, season_id, quest_date, slot, quest_id, progress, target, completed, rewarded, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, 0, 0, CURRENT_TIMESTAMP)
                    """,
                    (user_id, season_id, quest_date, slot, quest_id, int(quest["target"])),
                )
                slot += 1
            await db.commit()
            cur = await db.execute(
                """
                SELECT user_id, season_id, quest_date, slot, quest_id, progress, target, completed, rewarded
                FROM banana_pass_special_quests
                WHERE user_id = ? AND season_id = ? AND quest_date = ?
                ORDER BY slot
                """,
                (user_id, season_id, quest_date),
            )
            return [dict(row) for row in await cur.fetchall()]
        finally:
            await db.close()


async def get_pass_profile(user_id: int) -> Dict:
    user_data = await ensure_pass_user(user_id)
    xp = int(user_data.get("xp", 0) or 0)
    post_pass_complete = is_post_pass_complete(xp)
    quests = [] if post_pass_complete else await ensure_daily_quests(user_id)
    weekly_quests = [] if post_pass_complete else await ensure_weekly_quests(user_id)
    special_quests = await ensure_special_quests(user_id) if post_pass_complete else []
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
        "claims": await get_claimed_rewards_map(user_id),
        "active": is_pass_active(),
    }


async def buy_pass_premium(user_id: int) -> Tuple[bool, str]:
    if not is_pass_active():
        return False, "Сейчас сезон ADMIN PASS не активен."
    user_data = await ensure_pass_user(user_id)
    if int(user_data.get("premium_active", 0) or 0) == 1:
        return False, "Premium-доступ уже активирован."
    balances = load_balances_data()
    bal = ensure_balance_record(balances, user_id)
    cost = int(CURRENT_PASS_SEASON["premium_cost_bananas"])
    if int(bal.get("bananas", 0) or 0) < cost:
        return False, f"Нужно {cost} коинов для покупки Premium."
    bal["bananas"] = int(bal.get("bananas", 0) or 0) - cost
    save_balances_data(balances)
    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.execute(
                "UPDATE banana_pass_users SET premium_active = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND season_id = ?",
                (user_id, CURRENT_PASS_SEASON["id"]),
            )
            await db.execute(
                "DELETE FROM banana_pass_daily_quests WHERE user_id = ? AND season_id = ? AND quest_date = ?",
                (user_id, CURRENT_PASS_SEASON["id"], _today_str()),
            )
            await db.commit()
        finally:
            await db.close()
    await ensure_daily_quests(user_id)
    return True, f"Premium ADMIN PASS активирован за {cost} коинов. Открыто 20 обычных заданий и 5 элитных."


async def award_pass_reward(user_id: int, reward: Dict) -> str:
    reward_type = reward["type"]
    if reward_type == "money":
        balances = load_balances_data()
        bal = ensure_balance_record(balances, user_id)
        bal["wallet"] = int(bal.get("wallet", 0) or 0) + int(reward["amount"])
        save_balances_data(balances)
        return f"{reward['amount']:,}₽".replace(",", ".")
    if reward_type == "bananas":
        balances = load_balances_data()
        bal = ensure_balance_record(balances, user_id)
        bal["bananas"] = int(bal.get("bananas", 0) or 0) + int(reward["amount"])
        save_balances_data(balances)
        return f"{reward['amount']} коинов"
    if reward_type == "vip_days":
        balances = load_balances_data()
        bal = ensure_balance_record(balances, user_id)
        now = datetime.now()
        current_vip = bal.get("vip_until")
        start_dt = now
        if current_vip:
            try:
                vip_dt = datetime.fromisoformat(str(current_vip))
                if vip_dt > now:
                    start_dt = vip_dt
            except Exception:
                pass
        bal["vip_until"] = (start_dt + timedelta(days=int(reward["days"]))).isoformat()
        save_balances_data(balances)
        return f"VIP на {reward['days']} дней"
    if reward_type == "case":
        case_id = await add_user_case(user_id, reward["case_type"])
        case_name = CASE_DEFS.get(reward["case_type"], {}).get("name", reward["case_type"])
        return f"Кейс «{case_name}» #{case_id}"
    if reward_type == "item":
        await add_item(user_id, reward["item_type"], reward["item_name"], int(reward.get("item_value", 0) or 0))
        return reward["item_name"]
    if reward_type == "business":
        if reward["business_key"] in BUSINESSES_CATALOG:
            try:
                await add_business(user_id, reward["business_key"])
                return BUSINESSES_CATALOG[reward["business_key"]]["name"]
            except ValueError:
                pass
        balances = load_balances_data()
        bal = ensure_balance_record(balances, user_id)
        bal["wallet"] = int(bal.get("wallet", 0) or 0) + int(reward.get("fallback_money", 0) or 0)
        save_balances_data(balances)
        return f"{int(reward.get('fallback_money', 0)):,}₽".replace(",", ".")
    return reward.get("title", "Награда")


async def claim_pass_reward(user_id: int, track: str) -> Tuple[bool, str]:
    if track not in {"standard", "premium"}:
        return False, "Неизвестный тип награды."
    profile = await get_pass_profile(user_id)
    if not profile["active"]:
        return False, "Сезон ADMIN PASS сейчас не активен."
    if track == "premium" and not profile["premium_active"]:
        return False, "Premium-доступ не активирован."
    reward_map = get_reward_track(track)
    claims = profile["claims"]
    unlocked_levels = int(profile["unlocked_levels"])
    max_level = int(CURRENT_PASS_SEASON["max_level"])
    levels_to_claim = []
    for level in range(1, unlocked_levels + 1):
        if level > max_level:
            break
        if (level, track) in claims:
            continue
        reward = reward_map.get(level)
        if not reward:
            continue
        levels_to_claim.append((level, reward))

    if not levels_to_claim:
        return False, "Сейчас нет доступных наград для получения."

    claimed_lines = []
    for level, reward in levels_to_claim:
        reward_title = await award_pass_reward(user_id, reward)
        claimed_lines.append(f"{level}. {reward_title}")

    async with DB_WRITE_LOCK:
        db = await connect_sqlite()
        try:
            await db.executemany(
                "INSERT INTO banana_pass_claims (user_id, season_id, level, track, claimed_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                [(user_id, CURRENT_PASS_SEASON["id"], level, track) for level, _reward in levels_to_claim],
            )
            await db.commit()
        finally:
            await db.close()

    track_title = "Premium" if track == "premium" else "Standart"
    return True, (
        f"Забраны все доступные награды {track_title}: {len(levels_to_claim)} шт.\n"
        + "\n".join(claimed_lines)
    )


async def record_pass_progress(user_id: int, event_type: str, amount: int = 1, bot=None) -> List[str]:
    if not is_pass_active() or amount <= 0:
        return []
    quests = await ensure_daily_quests(user_id)
    completed_messages: List[str] = []
    xp_total = 0
    season_id = CURRENT_PASS_SEASON["id"]
    quest_date = _today_str()

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


def format_reward_short(track: str, level: int) -> str:
    reward = get_reward_track(track).get(level)
    return reward.get("title", "—") if reward else "—"


async def build_pass_text(user_id: int) -> str:
    profile = await get_pass_profile(user_id)
    xp = int(profile["xp"])
    level = int(profile["level"])
    unlocked = int(profile["unlocked_levels"])
    xp_per_level = int(CURRENT_PASS_SEASON["xp_per_level"])
    progress_in_level = xp % xp_per_level
    claims = profile["claims"]
    standard_available = sum(1 for lvl in range(1, unlocked + 1) if lvl in STANDARD_PASS_REWARDS and (lvl, "standard") not in claims)
    premium_available = sum(1 for lvl in range(1, unlocked + 1) if lvl in PREMIUM_PASS_REWARDS and (lvl, "premium") not in claims)
    premium_status = "активен" if profile["premium_active"] else f"не активен ({CURRENT_PASS_SEASON['premium_cost_bananas']} коинов)"
    lines = [
        f"🪙 {CURRENT_PASS_SEASON['title']}",
        CURRENT_PASS_SEASON["subtitle"],
        "",
        f"🕯 До конца сезона: {_season_end_left_text()}",
        f"🏁 Уровень пропуска: {level}/{CURRENT_PASS_SEASON['max_level']}",
        f"⭐ XP: {xp}",
        f"📈 Прогресс уровня: {progress_in_level}/{xp_per_level}",
        f"🎟 Standart: активен",
        f"?? Premium: {premium_status}",
        f"🧭 Следующий сезон: {CURRENT_PASS_SEASON['next_season_title']}",
        "",
        f"🎁 Можно забрать Standard: {standard_available}",
        f"🎁 Можно забрать Premium: {premium_available}",
        "",
        "Ближайшие уровни:",
    ]
    preview_start = max(1, level)
    preview_end = min(CURRENT_PASS_SEASON["max_level"], preview_start + 5)
    for lvl in range(preview_start, preview_end + 1):
        lines.append(f"{lvl}. S: {format_reward_short('standard', lvl)} | P: {format_reward_short('premium', lvl)}")
    lines.append("")
    lines.append("Команды: /pass, /квесты, /pass premium, /pass claim")
    return "\n".join(lines)


async def build_quests_text(user_id: int) -> str:
    profile = await get_pass_profile(user_id)
    regular = [quest for quest in profile["quests"] if int(quest.get("is_elite", 0) or 0) == 0]
    elite = [quest for quest in profile["quests"] if int(quest.get("is_elite", 0) or 0) == 1]
    weekly = await ensure_weekly_quests(user_id)
    completed_regular = sum(1 for quest in regular if int(quest.get("completed", 0) or 0) == 1)
    completed_elite = sum(1 for quest in elite if int(quest.get("completed", 0) or 0) == 1)
    completed_weekly = sum(1 for quest in weekly if int(quest.get("completed", 0) or 0) == 1)

    now = _now_msk()
    next_day = now.date() + timedelta(days=1)
    daily_refresh_at = MSK_TZ.localize(datetime.combine(next_day, time.min))
    daily_delta = daily_refresh_at - now
    daily_hours = max(0, int(daily_delta.total_seconds())) // 3600
    daily_minutes = (max(0, int(daily_delta.total_seconds())) % 3600) // 60
    daily_refresh_info = f"{daily_refresh_at.strftime('%d.%m %H:%M')} МСК (через {daily_hours}ч. {daily_minutes}м.)"

    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = now.date() + timedelta(days=days_until_monday)
    weekly_refresh_at = MSK_TZ.localize(datetime.combine(next_monday, time.min))
    weekly_delta = weekly_refresh_at - now
    weekly_days = max(0, int(weekly_delta.total_seconds())) // 86400
    weekly_hours = (max(0, int(weekly_delta.total_seconds())) % 86400) // 3600
    weekly_minutes = (max(0, int(weekly_delta.total_seconds())) % 3600) // 60
    weekly_refresh_info = f"{weekly_refresh_at.strftime('%d.%m %H:%M')} МСК (через {weekly_days}д. {weekly_hours}ч. {weekly_minutes}м.)"

    lines = [
        "🗂 Задания ADMIN PASS",
        f"📅 Сегодня: {_today_str()}",
        f"🕯 До конца сезона: {_season_end_left_text()}",
        "",
        f"🎟 Обычные задания: {completed_regular}/{len(regular)}",
        f"⏱ Обновление: {daily_refresh_info}",
    ]
    for quest in regular:
        meta = PASS_QUEST_POOL.get(str(quest["quest_id"]), {})
        done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "⏳"
        lines.append(f"{done} {meta.get('title', quest['quest_id'])} | {quest['progress']}/{quest['target']} | {quest['xp_reward']} XP")

    lines.append("")
    lines.append(f"🔥 Элитные задания: {completed_elite}/{len(elite)}")
    lines.append(f"⏱ Обновление: {daily_refresh_info}")
    for quest in elite:
        meta = PASS_QUEST_POOL.get(str(quest["quest_id"]), {})
        done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "🔥"
        lines.append(f"{done} {meta.get('title', quest['quest_id'])} | {quest['progress']}/{quest['target']} | {quest['xp_reward']} XP")

    if weekly:
        lines.append("")
        lines.append(f"📆 Недельные задания: {completed_weekly}/{len(weekly)}")
        lines.append(f"⏱ Обновление: {weekly_refresh_info}")
        for quest in weekly:
            meta = WEEKLY_PASS_QUEST_POOL.get(str(quest["quest_id"]), {})
            done = "✅" if int(quest.get("completed", 0) or 0) == 1 else "◆"
            lines.append(f"{done} {meta.get('title', quest['quest_id'])} | {quest['progress']}/{quest['target']} | {quest['xp_reward']} XP")
    return "\n".join(lines)
