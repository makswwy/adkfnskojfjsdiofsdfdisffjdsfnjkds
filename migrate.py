import sqlite3, psycopg2
from urllib.parse import urlparse

sqlite_conn = sqlite3.connect('database.db')
pg_url = 'postgresql://bothost_db_4b8532d13210:Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ@node1.pghost.ru:16182/bothost_db_4b8532d13210'
parsed = urlparse(pg_url)
pg_conn = psycopg2.connect(host=parsed.hostname, port=parsed.port, dbname=parsed.path.lstrip('/'), user=parsed.username, password=parsed.password)
pg_cursor = pg_conn.cursor()

BATCH = 1000
tables = ['cases_log', 'user_cases', 'auction_items', 'user_effects', 'exchange_logs', 'admin_epoints', 'admin_daily_stats', 'tech_action_forms', 'admin_stats', 'admin_norms', 'reset_requests', 'epoints_spends', 'businesses', 'inventory', 'permanent_prize_effects', 'permanent_business_effects', 'economy_wipe_state', 'subscription_claims', 'banana_pass_users', 'banana_pass_claims', 'banana_pass_daily_quests', 'banana_pass_weekly_quests', 'banana_pass_special_quests']

for table in tables:
    print('  ' + table + '...', end=' ', flush=True)
    cols = sqlite_conn.execute('PRAGMA table_info(' + table + ')').fetchall()
    if not cols:
        print('SKIP')
        continue
    
    pg_cols = []
    for col in cols:
        name = col[1]
        type_ = col[2]
        if col[5] and name == 'id':
            pg_cols.append(name + ' SERIAL PRIMARY KEY')
        else:
            pg_type = type_.replace('INTEGER', 'BIGINT').replace('TIMESTAMP', 'TIMESTAMPTZ')
            pg_cols.append(name + ' ' + pg_type)
    
    create_sql = 'CREATE TABLE IF NOT EXISTS ' + table + ' (' + ', '.join(pg_cols) + ')'
    pg_cursor.execute('DROP TABLE IF EXISTS ' + table + ' CASCADE')
    pg_cursor.execute(create_sql)
    
    total = sqlite_conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
    print(str(total) + ' rows, ', end='', flush=True)
    
    offset = 0
    inserted = 0
    while offset < total:
        cursor = sqlite_conn.execute('SELECT * FROM ' + table + ' LIMIT ' + str(BATCH) + ' OFFSET ' + str(offset))
        rows = cursor.fetchall()
        if not rows:
            break
        
        col_names = [d[0] for d in cursor.description]
        placeholders = ','.join(['%s'] * len(col_names))
        cols_str = ','.join(col_names)
        
        for row in rows:
            try:
                pg_cursor.execute('INSERT INTO ' + table + ' (' + cols_str + ') VALUES (' + placeholders + ')', row)
                inserted += 1
            except:
                pass
        
        pg_conn.commit()
        offset += BATCH
        del rows
    
    print('OK (' + str(inserted) + ')')

print('\n\u2713 Готово! ' + str(len(tables)) + ' таблиц.')
sqlite_conn.close()
pg_conn.close()
