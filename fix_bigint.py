#!/usr/bin/env python3
"""
Фикс: меняет integer на text для колонок с большими числами.
"""

import psycopg2

PG_HOST = "node1.pghost.ru"
PG_PORT = 16182
PG_DB = "bothost_db_4b8532d13210"
PG_USER = "bothost_db_4b8532d13210"
PG_PASSWORD = "Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ"

FIXES = {
    "autonomous_countries": [
        "treasury", "economy_score", "military_score", "happiness",
        "infrastructure", "population", "population_capacity",
        "development_level", "cities_open"
    ],
    "countries": [
        "treasury", "economy_score", "military_score", "happiness",
        "infrastructure", "population", "population_capacity"
    ],
    "country_coalition_aid": ["amount"],
    "country_military_assets": [
        "soldiers", "equipment", "ammunition", "defense_points",
        "attack_points", "tanks", "air_defense", "missiles",
        "ships", "aircraft", "drones", "nuclear_missiles"
    ],
    "country_projects": ["cost"],
    "country_transactions": ["amount"],
    "country_wars": ["attacker_power", "defender_power"],
}


def main():
    print("Фикс: integer -> text для больших чисел")
    print("="*60)
    
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
    )
    cur = conn.cursor()
    
    fixed = 0
    for table_name, columns in FIXES.items():
        print(f"\nТаблица: {table_name}")
        for col in columns:
            try:
                cur.execute("""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = %s AND column_name = %s
                """, (table_name, col))
                row = cur.fetchone()
                if row and row[0] == 'integer':
                    cur.execute(f'ALTER TABLE "{table_name}" ALTER COLUMN "{col}" TYPE TEXT')
                    print(f"  ✅ {col}: integer -> text")
                    fixed += 1
                else:
                    print(f"  ⏭️ {col}: {row[0] if row else 'unknown'}")
            except Exception as e:
                print(f"  ⚠️ {col}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"Исправлено: {fixed} колонок")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
