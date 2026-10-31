# -*- coding: utf-8 -*-
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import database_pg_async as aiosqlite
from utils.sqlite_utils import run_locked_sqlite_write

DB_PATH = "database.db"
BUSINESS_COLLECT_COOLDOWN = timedelta(hours=24)
BUSINESS_PRODUCTS_PER_COLLECT = 5
DEFAULT_BUSINESS_PRODUCTS = 100
SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
BIG_SQLITE_INT_PREFIX = "big:"
MAX_BRANCHES_PER_BUSINESS = 3000

BUSINESSES_CATALOG: Dict[str, Dict] = {
    "grand_espresso": {
        "name": 'Комиссионный дом "Второй Шанс"',
        "price": 1_000_000,
        "base_income": 275_000,
        "income_min": 250_000,
        "income_max": 300_000,
    },
    "golden_croissant": {
        "name": 'Бистро "На Ходу"',
        "price": 3_000_000,
        "base_income": 400_000,
        "income_min": 300_000,
        "income_max": 500_000,
    },
    "fashion_house": {
        "name": 'Бутик "Северный Лоск"',
        "price": 6_000_000,
        "base_income": 550_000,
        "income_min": 500_000,
        "income_max": 600_000,
    },
    "gourmania": {
        "name": 'Ресторан "Лунный Берег"',
        "price": 13_000_000,
        "base_income": 1_500_000,
        "income_min": 1_000_000,
        "income_max": 2_000_000,
    },
    "global_market": {
        "name": 'Маркет "Круглые Сутки"',
        "price": 20_000_000,
        "base_income": 2_500_000,
        "income_min": 2_000_000,
        "income_max": 3_000_000,
    },
    "fuel_giant": {
        "name": 'Сеть АЗС "Импульс Ойл"',
        "price": 75_000_000,
        "base_income": 9_500_000,
        "income_min": 8_000_000,
        "income_max": 11_000_000,
    },
    "neboskreb": {
        "name": 'Девелоперская группа "Монолит Вектор"',
        "price": 200_000_000,
        "base_income": 22_500_000,
        "income_min": 20_000_000,
        "income_max": 25_000_000,
    },
    "imax_empire": {
        "name": 'Клуб развлечений "Золотая Фишка"',
        "price": 300_000_000,
        "base_income": 37_500_000,
        "income_min": 35_000_000,
        "income_max": 40_000_000,
    },
    "worldwide_holdings": {
        "name": 'Инвестхолдинг "Союз Капитал"',
        "price": 500_000_000,
        "base_income": 77_500_000,
        "income_min": 70_000_000,
        "income_max": 85_000_000,
    },
    "powercore": {
        "name": 'Биобанк "Helix Nova"',
        "price": 650_000_000,
        "base_income": 95_000_000,
        "income_min": 90_000_000,
        "income_max": 100_000_000,
    },
    "cybersoft": {
        "name": 'Верфь "Ocean Matrix"',
        "price": 750_000_000,
        "base_income": 120_000_000,
        "income_min": 110_000_000,
        "income_max": 130_000_000,
    },
    "neotech": {
        "name": 'Аэрокосмический концерн "Orion Dynamics"',
        "price": 1_000_000_000,
        "base_income": 165_000_000,
        "income_min": 150_000_000,
        "income_max": 180_000_000,
    },
    "aurora_motors": {
        "name": 'Премиум-автоконцерн "Aurora Motors"',
        "price": 60_000_000_000,
        "base_income": 13_500_000_000,
        "income_min": 12_500_000_000,
        "income_max": 14_500_000_000,
    },
    "skyline_resort": {
        "name": 'Сеть люкс-курортов "Skyline Resort"',
        "price": 120_000_000_000,
        "base_income": 27_000_000_000,
        "income_min": 25_000_000_000,
        "income_max": 29_000_000_000,
    },
    "quantum_labs": {
        "name": 'Технопарк "Quantum Labs"',
        "price": 240_000_000_000,
        "base_income": 54_000_000_000,
        "income_min": 50_000_000_000,
        "income_max": 58_000_000_000,
    },
    "imperial_shipyards": {
        "name": 'Судостроительный холдинг "Imperial Shipyards"',
        "price": 500_000_000_000,
        "base_income": 112_500_000_000,
        "income_min": 104_000_000_000,
        "income_max": 121_000_000_000,
    },
    "crystal_bank": {
        "name": 'Инвестбанк "Crystal Bank"',
        "price": 1_000_000_000_000,
        "base_income": 225_000_000_000,
        "income_min": 208_000_000_000,
        "income_max": 242_000_000_000,
    },
    "titanium_air": {
        "name": 'Авиагруппа "Titanium Air"',
        "price": 2_000_000_000_000,
        "base_income": 450_000_000_000,
        "income_min": 417_000_000_000,
        "income_max": 483_000_000_000,
    },
    "genesis_mining": {
        "name": 'Добывающая корпорация "Genesis Mining"',
        "price": 4_000_000_000_000,
        "base_income": 900_000_000_000,
        "income_min": 834_000_000_000,
        "income_max": 966_000_000_000,
    },
    "nova_media": {
        "name": 'Медиахолдинг "Nova Media"',
        "price": 8_000_000_000_000,
        "base_income": 1_800_000_000_000,
        "income_min": 1_668_000_000_000,
        "income_max": 1_932_000_000_000,
    },
    "orbital_logistics": {
        "name": 'Логистический альянс "Orbital Logistics"',
        "price": 15_000_000_000_000,
        "base_income": 3_375_000_000_000,
        "income_min": 3_126_000_000_000,
        "income_max": 3_624_000_000_000,
    },
    "zenith_estate": {
        "name": 'Девелоперская империя "Zenith Estate"',
        "price": 28_000_000_000_000,
        "base_income": 6_300_000_000_000,
        "income_min": 5_838_000_000_000,
        "income_max": 6_762_000_000_000,
    },
    "fusion_dynamics": {
        "name": 'Энергоконцерн "Fusion Dynamics"',
        "price": 45_000_000_000_000,
        "base_income": 10_125_000_000_000,
        "income_min": 9_384_000_000_000,
        "income_max": 10_866_000_000_000,
    },
    "atlas_defense": {
        "name": 'Оборонный гигант "Atlas Defense"',
        "price": 75_000_000_000_000,
        "base_income": 16_875_000_000_000,
        "income_min": 15_636_000_000_000,
        "income_max": 18_114_000_000_000,
    },
    "celestium_group": {
        "name": 'Глобальный конгломерат "Celestium Group"',
        "price": 120_000_000_000_000,
        "base_income": 27_000_000_000_000,
        "income_min": 25_020_000_000_000,
        "income_max": 28_980_000_000_000,
    },
    "solaris_biotech": {
        "name": 'Биотех-альянс "Solaris Biotech"',
        "price": 180_000_000_000_000,
        "base_income": 40_500_000_000_000,
        "income_min": 37_530_000_000_000,
        "income_max": 43_470_000_000_000,
    },
    "aurum_exchange": {
        "name": 'Биржевая группа "Aurum Exchange"',
        "price": 260_000_000_000_000,
        "base_income": 58_500_000_000_000,
        "income_min": 54_210_000_000_000,
        "income_max": 62_790_000_000_000,
    },
    "deepwave_energy": {
        "name": 'Энергофлот "Deepwave Energy"',
        "price": 380_000_000_000_000,
        "base_income": 85_500_000_000_000,
        "income_min": 79_230_000_000_000,
        "income_max": 91_770_000_000_000,
    },
    "nexus_infrastructure": {
        "name": 'Инфраструктурный трест "Nexus Infrastructure"',
        "price": 550_000_000_000_000,
        "base_income": 123_750_000_000_000,
        "income_min": 114_675_000_000_000,
        "income_max": 132_825_000_000_000,
    },
    "starlight_media": {
        "name": 'Медиавселенная "Starlight Media"',
        "price": 800_000_000_000_000,
        "base_income": 180_000_000_000_000,
        "income_min": 166_800_000_000_000,
        "income_max": 193_200_000_000_000,
    },
    "hyperion_foods": {
        "name": 'Агрохолдинг "Hyperion Foods"',
        "price": 1_200_000_000_000_000,
        "base_income": 270_000_000_000_000,
        "income_min": 250_200_000_000_000,
        "income_max": 289_800_000_000_000,
    },
    "vertex_security": {
        "name": 'Технощит "Vertex Security"',
        "price": 1_800_000_000_000_000,
        "base_income": 405_000_000_000_000,
        "income_min": 375_300_000_000_000,
        "income_max": 434_700_000_000_000,
    },
    "eclipse_industries": {
        "name": 'Промышленная группа "Eclipse Industries"',
        "price": 2_700_000_000_000_000,
        "base_income": 607_500_000_000_000,
        "income_min": 562_950_000_000_000,
        "income_max": 652_050_000_000_000,
    },
    "astral_finance": {
        "name": 'Финансовая империя "Astral Finance"',
        "price": 4_000_000_000_000_000,
        "base_income": 900_000_000_000_000,
        "income_min": 834_000_000_000_000,
        "income_max": 966_000_000_000_000,
    },
    "omega_syndicate": {
        "name": 'Мегакорпорация "Omega Syndicate"',
        "price": 6_000_000_000_000_000,
        "base_income": 1_350_000_000_000_000,
        "income_min": 1_251_000_000_000_000,
        "income_max": 1_449_000_000_000_000,
    },
}


def _scaled_business_meta(name: str, price: int) -> Dict:
    return {
        "name": name,
        "price": int(price),
        "base_income": int(price) * 225 // 1000,
        "income_min": int(price) * 2085 // 10000,
        "income_max": int(price) * 2415 // 10000,
    }


ADDITIONAL_BUSINESSES = {
    key: _scaled_business_meta(name, price)
    for key, name, price in [
        ("helios_robotics", 'Робототехнический кластер "Helios Robotics"', 8_500_000_000_000_000),
        ("polar_pharma", 'Фармацевтический холдинг "Polar Pharma"', 12_000_000_000_000_000),
        ("emerald_ports", 'Портовый оператор "Emerald Ports"', 17_000_000_000_000_000),
        ("horizon_cloud", 'Облачная платформа "Horizon Cloud"', 24_000_000_000_000_000),
        ("titanium_rail", 'Железнодорожная сеть "Titanium Rail"', 34_000_000_000_000_000),
        ("velvet_hotels", 'Гостиничная группа "Velvet Hotels"', 48_000_000_000_000_000),
        ("arcadia_games", 'Игровая студия "Arcadia Games"', 68_000_000_000_000_000),
        ("nova_textiles", 'Текстильный концерн "Nova Textiles"', 95_000_000_000_000_000),
        ("summit_logistics", 'Грузовой оператор "Summit Logistics"', 135_000_000_000_000_000),
        ("sapphire_water", 'Водный холдинг "Sapphire Water"', 190_000_000_000_000_000),
        ("comet_delivery", 'Курьерская сеть "Comet Delivery"', 270_000_000_000_000_000),
        ("ironforge_tools", 'Заводской альянс "Ironforge Tools"', 380_000_000_000_000_000),
        ("prism_fintech", 'Финтех-платформа "Prism Fintech"', 540_000_000_000_000_000),
        ("cedar_medicine", 'Медицинская сеть "Cedar Medicine"', 760_000_000_000_000_000),
        ("lunar_electronics", 'Электронный концерн "Lunar Electronics"', 790_000_000_000_000_000),
        ("meridian_airports", 'Аэропортовый оператор "Meridian Airports"', 820_000_000_000_000_000),
        ("citadel_insurance", 'Страховая группа "Citadel Insurance"', 850_000_000_000_000_000),
        ("amber_foods", 'Пищевой холдинг "Amber Foods"', 880_000_000_000_000_000),
        ("vertex_cargo", 'Карго-союз "Vertex Cargo"', 910_000_000_000_000_000),
        ("aurora_stream", 'Стриминговая сеть "Aurora Stream"', 940_000_000_000_000_000),
        ("stellar_university", 'Образовательный холдинг "Stellar University"', 970_000_000_000_000_000),
        ("obsidian_mall", 'Торговый комплекс "Obsidian Mall"', 1_000_000_000_000_000_000),
        ("silverline_bank", 'Банк развития "Silverline Bank"', 1_030_000_000_000_000_000),
        ("domino_retail", 'Ритейл-сеть "Domino Retail"', 1_040_000_000_000_000_000),
        ("neon_advertising", 'Рекламная группа "Neon Advertising"', 1_050_000_000_000_000_000),
        ("atlas_medtech", 'Медтех-корпорация "Atlas MedTech"', 1_250_000_000_000_000_000),
        ("forge_automatics", 'Машиностроение "Forge Automatics"', 1_450_000_000_000_000_000),
        ("orbit_farms", 'Вертикальные фермы "Orbit Farms"', 1_650_000_000_000_000_000),
        ("crystal_datacenter", 'Дата-центр "Crystal Datacenter"', 1_850_000_000_000_000_000),
        ("flame_power", 'Теплоэнергетика "Flame Power"', 2_050_000_000_000_000_000),
        ("mirage_cinema", 'Киноконцерн "Mirage Cinema"', 2_250_000_000_000_000_000),
        ("canyon_motors", 'Транспортный завод "Canyon Motors"', 2_450_000_000_000_000_000),
        ("azure_shipping", 'Судоходная линия "Azure Shipping"', 2_650_000_000_000_000_000),
        ("noble_telecom", 'Телеком-группа "Noble Telecom"', 2_850_000_000_000_000_000),
        ("aurum_luxury", 'Дом роскоши "Aurum Luxury"', 3_050_000_000_000_000_000),
        ("quantum_finance", 'Квантовый фонд "Quantum Finance"', 3_250_000_000_000_000_000),
        ("titan_harbor", 'Гавань "Titan Harbor"', 3_450_000_000_000_000_000),
        ("terra_builders", 'Стройтрест "Terra Builders"', 3_650_000_000_000_000_000),
        ("zenith_spaceports", 'Космопорт "Zenith Spaceports"', 3_850_000_000_000_000_000),
        ("phoenix_energy", 'Энергосистема "Phoenix Energy"', 4_050_000_000_000_000_000),
        ("monolith_ai", 'AI-лаборатория "Monolith AI"', 4_250_000_000_000_000_000),
        ("ivory_biomed", 'Биомедицинский центр "Ivory Biomed"', 4_450_000_000_000_000_000),
        ("eclipse_cloud", 'Облачный концерн "Eclipse Cloud"', 4_650_000_000_000_000_000),
        ("nova_semiconductors", 'Полупроводники "Nova Semiconductors"', 4_850_000_000_000_000_000),
        ("infinity_robotics", 'Робозавод "Infinity Robotics"', 5_050_000_000_000_000_000),
        ("crown_finance", 'Казначейство "Crown Finance"', 5_250_000_000_000_000_000),
        ("argentum_mining", 'Рудный картель "Argentum Mining"', 5_450_000_000_000_000_000),
        ("omega_ports", 'Межконтинентальные порты "Omega Ports"', 5_650_000_000_000_000_000),
        ("horizon_airways", 'Авиахолдинг "Horizon Airways"', 5_850_000_000_000_000_000),
        ("imperial_datagrid", 'Инфосеть "Imperial DataGrid"', 6_050_000_000_000_000_000),
        ("apex_nanotech", 'Нанотехнополис "Apex Nanotech"', 6_250_000_000_000_000_000),
        ("sovereign_estate", 'Земельный банк "Sovereign Estate"', 6_450_000_000_000_000_000),
        ("galaxy_media", 'Галактические медиа "Galaxy Media"', 6_650_000_000_000_000_000),
        ("terraforming_inc", 'Инженерная группа "Terraforming Inc."', 6_850_000_000_000_000_000),
        ("prism_defense", 'Оборонная система "Prism Defense"', 7_050_000_000_000_000_000),
        ("atlas_energy_grid", 'Энергокольцо "Atlas Energy Grid"', 7_250_000_000_000_000_000),
        ("obsidian_capital", 'Суверенный фонд "Obsidian Capital"', 7_450_000_000_000_000_000),
        ("chronos_labs", 'Исследовательский центр "Chronos Labs"', 7_650_000_000_000_000_000),
        ("titan_orbitals", 'Орбитальные станции "Titan Orbitals"', 7_850_000_000_000_000_000),
        ("singularity_ai", 'Нейросеть "Singularity AI"', 8_050_000_000_000_000_000),
        ("solaris_arcology", 'Аркология "Solaris Arcology"', 8_250_000_000_000_000_000),
        ("hyperion_gateway", 'Транзитный узел "Hyperion Gateway"', 8_450_000_000_000_000_000),
        ("celestia_holdings", 'Конгломерат "Celestia Holdings"', 8_650_000_000_000_000_000),
        ("eternity_exchange", 'Биржа "Eternity Exchange"', 8_850_000_000_000_000_000),
        ("banana_nexus", 'Метавселенная "Banana Nexus"', 9_050_000_000_000_000_000),
    ]
}
BUSINESSES_CATALOG.update(ADDITIONAL_BUSINESSES)


PURCHASEABLE_BUSINESS_KEYS = [
    "grand_espresso",
    "golden_croissant",
    "fashion_house",
    "gourmania",
    "global_market",
    "fuel_giant",
    "neboskreb",
    "imax_empire",
    "worldwide_holdings",
    "powercore",
    "cybersoft",
    "neotech",
    "aurora_motors",
    "skyline_resort",
    "quantum_labs",
    "imperial_shipyards",
    "crystal_bank",
    "titanium_air",
    "genesis_mining",
    "nova_media",
    "orbital_logistics",
    "zenith_estate",
    "fusion_dynamics",
    "atlas_defense",
    "celestium_group",
    "solaris_biotech",
    "aurum_exchange",
    "deepwave_energy",
    "nexus_infrastructure",
    "starlight_media",
    "hyperion_foods",
    "vertex_security",
    "eclipse_industries",
    "astral_finance",
    "omega_syndicate",
] + list(ADDITIONAL_BUSINESSES.keys())

# Совместимость со старыми филиалами в базе данных.
BUSINESSES_CATALOG["premium_detailing"] = dict(BUSINESSES_CATALOG["golden_croissant"])
BUSINESSES_CATALOG["agro_empire"] = dict(BUSINESSES_CATALOG["global_market"])
BUSINESSES_CATALOG["iron_world"] = dict(BUSINESSES_CATALOG["global_market"])
BUSINESSES_CATALOG["empire_realty"] = dict(BUSINESSES_CATALOG["neboskreb"])
BUSINESSES_CATALOG["megadrive"] = dict(BUSINESSES_CATALOG["fuel_giant"])
BUSINESSES_CATALOG["royal_beauty"] = dict(BUSINESSES_CATALOG["fashion_house"])
BUSINESSES_CATALOG["golden_trust"] = dict(BUSINESSES_CATALOG["grand_espresso"])
BUSINESSES_CATALOG["diamond_crown"] = dict(BUSINESSES_CATALOG["gourmania"])

UPGRADE_BONUSES = {0: 0.0, 1: 0.10, 2: 0.25, 3: 0.50}
UPGRADE_BONUS_PERCENT = {0: 0, 1: 10, 2: 25, 3: 50}


def sqlite_int_or_text(value: int):
    value = int(value or 0)
    if -SQLITE_INTEGER_MAX <= value <= SQLITE_INTEGER_MAX:
        return value
    return f"{BIG_SQLITE_INT_PREFIX}{value}"


def sqlite_value_to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if text.startswith(BIG_SQLITE_INT_PREFIX):
        text = text[len(BIG_SQLITE_INT_PREFIX):]
    try:
        return int(text)
    except ValueError:
        return int(float(text))


def get_upgrade_cost_for_business(business_meta: Dict, new_level: int) -> int:
    base_price = int(business_meta.get("price", 0) or 0)
    level_multipliers = {
        1: 0.05,
        2: 0.10,
        3: 0.20,
    }
    multiplier = level_multipliers.get(int(new_level), 0.20)
    return max(100_000, int(base_price * multiplier))


async def _execute_business_write(query: str, params: Tuple) -> None:
    async def _write(db: aiosqlite.Connection) -> None:
        await db.execute(query, params)
        await db.commit()

    await run_locked_sqlite_write(_write)


async def add_business(user_id: int, business_key: str) -> int:
    async def _write(db: aiosqlite.Connection) -> int:
        cur = await db.execute(
            "SELECT COALESCE(MAX(branch_no), 0) FROM businesses WHERE user_id = ? AND business_key = ?",
            (user_id, business_key),
        )
        next_branch = int((await cur.fetchone())[0]) + 1
        if next_branch > MAX_BRANCHES_PER_BUSINESS:
            raise ValueError(f"Нельзя иметь больше {MAX_BRANCHES_PER_BUSINESS} филиалов одного типа бизнеса.")
        await db.execute(
            "INSERT INTO businesses (user_id, business_key, branch_no, products) VALUES (?, ?, ?, ?)",
            (user_id, business_key, next_branch, DEFAULT_BUSINESS_PRODUCTS),
        )
        await db.commit()
        return next_branch

    return await run_locked_sqlite_write(_write)


async def add_businesses_bulk(user_id: int, business_key: str, quantity: int) -> List[int]:
    quantity = max(1, int(quantity or 1))

    async def _write(db: aiosqlite.Connection) -> List[int]:
        cur = await db.execute(
            "SELECT COALESCE(MAX(branch_no), 0) FROM businesses WHERE user_id = ? AND business_key = ?",
            (user_id, business_key),
        )
        start_branch = int((await cur.fetchone())[0]) + 1
        end_branch = start_branch + quantity - 1
        if end_branch > MAX_BRANCHES_PER_BUSINESS:
            raise ValueError(f"Нельзя иметь больше {MAX_BRANCHES_PER_BUSINESS} филиалов одного типа бизнеса.")
        branch_numbers = list(range(start_branch, end_branch + 1))
        await db.executemany(
            "INSERT INTO businesses (user_id, business_key, branch_no, products) VALUES (?, ?, ?, ?)",
            [
                (user_id, business_key, branch_no, DEFAULT_BUSINESS_PRODUCTS)
                for branch_no in branch_numbers
            ],
        )
        await db.commit()
        return branch_numbers

    return await run_locked_sqlite_write(_write)


async def get_user_businesses(user_id: int) -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT b.id, b.business_key, b.branch_no, b.upgrade_level, b.products, b.branch_balance,
                   b.last_collected_at
            FROM businesses b
            WHERE b.user_id = ?
            ORDER BY b.business_key, b.branch_no
            """,
            (user_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["branch_balance"] = sqlite_value_to_int(row.get("branch_balance", 0))
        row["meta"] = BUSINESSES_CATALOG.get(
            row["business_key"],
            {"name": row["business_key"], "base_income": 0, "price": 0},
        )
    return rows


async def get_all_business_branch_counts() -> Dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, COUNT(*) FROM businesses GROUP BY user_id"
        )
        rows = await cur.fetchall()
    return {str(int(user_id)): int(count) for user_id, count in rows}


async def get_business_by_id(user_id: int, business_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT b.id, b.business_key, b.branch_no, b.upgrade_level, b.products, b.branch_balance,
                   b.last_collected_at
            FROM businesses b
            WHERE b.user_id = ? AND b.id = ?
            """,
            (user_id, business_id),
        )
        row = await cur.fetchone()
    if not row:
        return None
    item = dict(row)
    item["branch_balance"] = sqlite_value_to_int(item.get("branch_balance", 0))
    item["meta"] = BUSINESSES_CATALOG.get(
        item["business_key"],
        {"name": item["business_key"], "base_income": 0, "price": 0},
    )
    return item


def get_business_collect_ready_at(business: Dict) -> Optional[datetime]:
    raw_value = business.get("last_collected_at")
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value)) + BUSINESS_COLLECT_COOLDOWN
    except (TypeError, ValueError):
        return None


def get_business_collect_seconds_left(business: Dict) -> int:
    ready_at = get_business_collect_ready_at(business)
    if ready_at is None:
        return 0
    return max(0, int((ready_at - datetime.now()).total_seconds()))



def calculate_business_income(business: Dict) -> int:
    base_income = int(business.get("meta", {}).get("base_income", 0) or 0)
    level = int(business.get("upgrade_level", 0) or 0)
    upgrade_percent = UPGRADE_BONUS_PERCENT.get(level, 0)
    # Талисманы отключены — не применяем talisman_percent
    return (
        base_income
        + (base_income * upgrade_percent // 100)
    )



async def delete_business_branch(user_id: int, business_id: int) -> Tuple[bool, Optional[Dict]]:
    business = await get_business_by_id(user_id, business_id)
    if not business:
        return False, None
    await _execute_business_write(
        "DELETE FROM businesses WHERE id = ? AND user_id = ?",
        (business_id, user_id),
    )
    return True, business


async def delete_business_group(user_id: int, business_key: str) -> Tuple[int, Optional[Dict]]:
    businesses = await get_user_businesses(user_id)
    target_branches = [biz for biz in businesses if biz["business_key"] == business_key]
    if not target_branches:
        return 0, None
    await _execute_business_write(
        "DELETE FROM businesses WHERE user_id = ? AND business_key = ?",
        (user_id, business_key),
    )
    return len(target_branches), target_branches[0]


async def upgrade_business(user_id: int, business_id: int) -> Tuple[bool, str, int]:
    business = await get_business_by_id(user_id, business_id)
    if not business:
        return False, "Филиал не найден.", 0
    level = int(business.get("upgrade_level") or 0)
    if level >= 3:
        return False, "Филиал уже на максимальном уровне.", 0
    new_level = level + 1
    cost = get_upgrade_cost_for_business(business["meta"], new_level)
    await _execute_business_write(
        "UPDATE businesses SET upgrade_level = ? WHERE id = ? AND user_id = ?",
        (new_level, business_id, user_id),
    )
    return True, f"Улучшение до уровня {new_level} применено.", cost


async def refill_products(user_id: int, business_id: int, amount: int, max_products: int = 100) -> Tuple[bool, str, int]:
    business = await get_business_by_id(user_id, business_id)
    if not business:
        return False, "Филиал не найден.", 0
    current = int(business.get("products") or 0)
    if current >= max_products:
        return False, "Продукты уже заполнены.", 0
    add_amount = max(1, min(amount, max_products - current))
    await _execute_business_write(
        "UPDATE businesses SET products = products + ? WHERE id = ? AND user_id = ?",
        (add_amount, business_id, user_id),
    )
    return True, f"Пополнено на {add_amount} ед.", add_amount


async def collect_income(user_id: int, business_id: int) -> Tuple[bool, str, int]:
    if business_id is not None:
        business = await get_business_by_id(user_id, business_id)
    else:
        businesses = await get_user_businesses(user_id)
        business = None
        for b in businesses:
            if b.get("id") is None:
                business = b
                break
    if not business:
        return False, "Филиал не найден.", 0
    seconds_left = get_business_collect_seconds_left(business)
    if seconds_left > 0:
        hours = seconds_left // 3600
        minutes = (seconds_left % 3600) // 60
        return False, f"Сбор будет доступен через {hours}ч. {minutes}м.", 0
    products_to_spend = BUSINESS_PRODUCTS_PER_COLLECT
    current_products = int(business.get("products") or 0)
    if current_products < products_to_spend:
        return False, f"Недостаточно продуктов. Для сбора нужно минимум {products_to_spend}.", 0
    
    # Рассчитываем количество пропущенных дней
    last_collected_at = business.get("last_collected_at")
    days_elapsed = 1  # По умолчанию 1 день
    now_dt = datetime.now()
    if last_collected_at:
        try:
            last_dt = datetime.fromisoformat(str(last_collected_at))
            delta = now_dt - last_dt
            total_seconds = delta.total_seconds()
            days_elapsed = max(1, int(total_seconds / 86400))  # 86400 секунд в дне
        except (TypeError, ValueError):
            days_elapsed = 1
    
    # Рассчитываем доход с учётом пропущенных дней
    base_income = int(business.get("meta", {}).get("base_income", 0) or 0)
    level = int(business.get("upgrade_level", 0) or 0)
    upgrade_percent = UPGRADE_BONUS_PERCENT.get(level, 0)
    daily_income = (
        base_income
        + (base_income * upgrade_percent // 100)
    )
    final_income = daily_income * days_elapsed
    
    current_balance = int(business.get("branch_balance", 0) or 0)
    new_balance = current_balance + final_income
    await _execute_business_write(
        "UPDATE businesses SET products = products - ?, branch_balance = ?, last_collected_at = ? "
        "WHERE user_id = ? AND business_key = ? AND branch_no = ?",
        (
            products_to_spend,
            sqlite_int_or_text(new_balance),
            datetime.now().isoformat(),
            user_id,
            business.get("business_key"),
            business.get("branch_no"),
        ),
    )
    return True, f"Доход собран за {days_elapsed} дн. Списано продуктов: {products_to_spend}.", final_income











def get_business_talisman_bonus_percent(branch: dict) -> int:
    return int(branch.get("talisman_bonus_percent") or 0)


async def activate_business_talisman(user_id: int, business_id: int, temporary_talisman_bonus: int) -> tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        if business_id and business_id != 0:
            await db.execute(
                "UPDATE businesses SET talisman_bonus_percent = ?, talisman_active = 1 WHERE id = ? AND user_id = ?",
                (temporary_talisman_bonus, business_id, user_id),
            )
        else:
            # Fallback: update by user_id only (last business)
            await db.execute(
                "UPDATE businesses SET talisman_bonus_percent = ?, talisman_active = 1 WHERE user_id = ? AND talisman_bonus_percent = 0",
                (temporary_talisman_bonus, user_id),
            )
        await db.commit()
    return True, "Талисман успешно активирован."
