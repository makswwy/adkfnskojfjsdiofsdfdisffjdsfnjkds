import os, psycopg2
from flask import Flask, render_template, request, redirect, session

app = Flask(__name__, template_folder='templates', static_folder='.')
app.secret_key = 'logs-secret-key'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_CONFIG = {
    'host': 'node1.pghost.ru', 'port': 16182,
    'dbname': 'bothost_db_4b8532d13210',
    'user': 'bothost_db_4b8532d13210',
    'password': 'Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ',
}

def get_db():
    return psycopg2.connect(**DB_CONFIG)

def get_admin_context():
    return {'session_user': session.get('user'), 'vk_login_ready': True}

def get_stats(page, limit=50):
    return {'page': page, 'limit': limit, 'db_exists': True}

@app.route('/')
def index():
    return redirect('/exchangelogs')

@app.route('/exchangelogs')
def exchangelogs():
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, target_id, status, log, created_at FROM exchange_logs ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM exchange_logs")
        total = cur.fetchone()[0]
        cur.close(); conn.close()
        logs = [{'id': r[0], 'user_id': r[1], 'target_id': r[2], 'status': r[3], 'log': r[4], 'created_at': r[5]} for r in rows]
    except Exception as e:
        logs = []
        total = 0
    return render_template('index.html',
        page_title='Логи обменов',
        page_description='Логи обменов бота',
        stats=get_stats('exchangelogs', limit),
        admin=get_admin_context(),
        logs=logs,
        total=total,
    )

@app.route('/bananalogs')
def bananalogs():
    limit = int(request.args.get('limit', 50))
    return render_template('index.html',
        page_title='Banana Logs',
        page_description='Логи бананов бота',
        stats=get_stats('bananalogs', limit),
        admin=get_admin_context(),
        logs=[],
        total=0,
    )

@app.route('/economy')
def economy():
    limit = int(request.args.get('limit', 50))
    return render_template('index.html',
        page_title='Логи экономики',
        page_description='Логи экономики бота',
        stats=get_stats('economy', limit),
        admin=get_admin_context(),
        logs=[],
        total=0,
    )

@app.route('/botlogs')
def botlogs():
    limit = int(request.args.get('limit', 50))
    return render_template('index.html',
        page_title='Логи бота',
        page_description='Логи бота',
        stats=get_stats('botlogs', limit),
        admin=get_admin_context(),
        logs=[],
        total=0,
    )

@app.route('/requests')
def requests_page():
    limit = int(request.args.get('limit', 50))
    return render_template('index.html',
        page_title='Формы',
        page_description='Формы пользователей',
        stats=get_stats('requests', limit),
        admin=get_admin_context(),
        logs=[],
        total=0,
    )

@app.route('/auth/vk/start')
def auth_vk_start():
    return redirect('https://oauth.vk.com/authorize?client_id=YOUR_VK_APP_ID&display=page&redirect_uri=http://localhost:5000/auth/vk/callback&scope=offline&response_type=code&v=5.199')

@app.route('/auth/vk/callback')
def auth_vk_callback():
    code = request.args.get('code')
    if code:
        session['user'] = {'name': 'Admin', 'role_label': 'Администратор', 'vk_url': '#'}
    return redirect('/bananalogs')

@app.route('/auth/logout')
def auth_logout():
    session.clear()
    return redirect('/bananalogs')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
