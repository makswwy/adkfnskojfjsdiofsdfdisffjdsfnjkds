<?php
$db_host = 'node1.pghost.ru';
$db_port = 16182;
$db_name = 'bothost_db_4b8532d13210';
$db_user = 'bothost_db_4b8532d13210';
$db_pass = 'Y-auDO4Xwu47yOrqYqQtX-rJA4_v4IMQaElr86-91sQ';

try {
    $pdo = new PDO("pgsql:host=$db_host;port=$db_port;dbname=$db_name", $db_user, $db_pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $db_connected = true;
} catch (PDOException $e) {
    $db_connected = false;
    $db_error = $e->getMessage();
}

session_start();

$vk_app_id = 'YOUR_VK_APP_ID';
$vk_app_secret = 'YOUR_VK_APP_SECRET';

function get_page_title($page) {
    $titles = array('bananalogs' => 'Banana Logs', 'economy' => 'Логи экономики', 'botlogs' => 'Логи бота', 'exchangelogs' => 'Логи обменов', 'requests' => 'Формы');
    return isset($titles[$page]) ? $titles[$page] : 'Banana Logs';
}

function get_page_desc($page) {
    $descs = array('bananalogs' => 'Логи бананов бота', 'economy' => 'Логи экономики бота', 'botlogs' => 'Логи бота', 'exchangelogs' => 'Логи обменов', 'requests' => 'Формы пользователей');
    return isset($descs[$page]) ? $descs[$page] : 'Логи';
}

function get_admin_context() {
    global $vk_app_id;
    if (isset($_SESSION['user'])) {
        return array('session_user' => $_SESSION['user'], 'vk_login_ready' => true);
    }
    return array('session_user' => null, 'vk_login_ready' => !empty($vk_app_id) && $vk_app_id !== 'YOUR_VK_APP_ID');
}

// Handle /auth/vk/session POST request
if (strpos($request_uri, '/auth/vk/session') !== false && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $input = json_decode(file_get_contents('php://input'), true);
    $token = $input['access_token'] ?? null;
    if ($token) {
        $_SESSION['user'] = array('name' => 'Admin', 'role_label' => 'Администратор', 'vk_url' => '#');
        header('Content-Type: application/json');
        echo json_encode(array('ok' => true));
    } else {
        header('Content-Type: application/json');
        http_response_code(400);
        echo json_encode(array('ok' => false, 'error' => 'No access_token'));
    }
    exit;
}

$request_uri = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$limit = isset($_GET['limit']) ? (int)$_GET['limit'] : 50;
$user_id = isset($_GET['user_id']) ? (int)$_GET['user_id'] : null;
$status = isset($_GET['status']) ? $_GET['status'] : 'open';

$page = 'bananalogs';
if (strpos($request_uri, '/economy') !== false) $page = 'economy';
elseif (strpos($request_uri, '/botlogs') !== false) $page = 'botlogs';
elseif (strpos($request_uri, '/exchangelogs') !== false) $page = 'exchangelogs';
elseif (strpos($request_uri, '/requests') !== false) $page = 'requests';

if (strpos($request_uri, '/auth/vk/start') !== false) {
    header('Location: https://oauth.vk.com/authorize?client_id=' . $vk_app_id . '&display=page&redirect_uri=http://' . $_SERVER['HTTP_HOST'] . '/auth/vk/callback&scope=offline&response_type=code&v=5.199');
    exit;
}
if (strpos($request_uri, '/auth/vk/callback') !== false) {
    if (isset($_GET['code'])) {
        $_SESSION['user'] = array('name' => 'Admin', 'role_label' => 'Администратор', 'vk_url' => '#');
    }
    header('Location: /bananalogs');
    exit;
}
if (strpos($request_uri, '/auth/logout') !== false) {
    session_destroy();
    header('Location: /bananalogs');
    exit;
}

$admin = get_admin_context();
$stats = array('page' => $page, 'limit' => $limit, 'db_exists' => $db_connected, 'user_filter' => $user_id, 'status_filter' => $status);

$rows = array();
$access_denied = false;
$denied_text = '';
$request_rows = array();
$request_types = array(array('reset', 'Обнуление'), array('return', 'Возврат'), array('other', 'Другое'));

if ($db_connected) {
    $table_map = array('bananalogs' => 'banana_logs', 'economy' => 'economy_logs', 'botlogs' => 'bot_logs', 'exchangelogs' => 'exchange_logs');
    if (isset($table_map[$page])) {
        $table = $table_map[$page];
        try {
            $query = "SELECT * FROM $table ORDER BY id DESC LIMIT :limit";
            $params = array(':limit' => $limit);
            if ($user_id) {
                $query = "SELECT * FROM $table WHERE user_id = :user_id ORDER BY id DESC LIMIT :limit";
                $params[':user_id'] = $user_id;
            }
            $stmt = $pdo->prepare($query);
            $stmt->execute($params);
            $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
        } catch (PDOException $e) {}
    }
}

$page_title = get_page_title($page);
$page_desc = get_page_desc($page);
?>
<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>BANANA MANAGER</title>
    <style>
        body{margin:0;font-family:"Segoe UI",sans-serif;color:#eef5fb;background:linear-gradient(180deg,#0b1014,#0f151c 62%,#0a0f14)}
        .wrap{max-width:1200px;margin:20px auto;padding:0 14px}
        .hero,.panel{background:#12181f;border:1px solid #2a3947;border-radius:16px;padding:20px;margin-bottom:16px}
        h1{margin:0 0 8px;font-size:32px}
        .nav{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
        .nav a{padding:8px 12px;border-radius:8px;border:1px solid #2a3947;background:#17212a;color:#94a8ba;text-decoration:none}
        .nav a.active{background:rgba(215,181,109,.16);border-color:rgba(215,181,109,.42);color:#f7e7b8;font-weight:700}
        .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
        .stat{padding:12px;border-radius:12px;background:#17212a}
        .stat span{color:#94a8ba;font-size:12px}
        .stat b{display:block;margin-top:4px;font-size:20px}
        .toolbar{padding:12px;background:#12181f;border:1px solid #2a3947;border-radius:12px;margin-bottom:16px}
        .toolbar form{display:flex;gap:8px;flex-wrap:wrap}
        .toolbar input,.toolbar select{padding:8px 10px;border-radius:8px;border:1px solid #2a3947;background:#17212a;color:#eef5fb}
        .toolbar button{padding:8px 12px;border-radius:8px;border:1px solid rgba(215,181,109,.34);background:rgba(215,181,109,.16);color:#f7e7b8;font-weight:700;cursor:pointer}
        .entry{padding:14px;border-radius:12px;background:#17212a;margin-bottom:10px}
        .chip{padding:4px 8px;border-radius:999px;background:#1b2631;font-size:11px;color:#94a8ba;margin-right:4px}
        .log-text{padding:10px;border-radius:8px;background:#12181f;margin-top:8px;white-space:pre-wrap}
        .empty{text-align:center;padding:20px;color:#94a8ba}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="hero">
            <h1>BANANA MANAGER</h1>
            <p style="color:#94a8ba"><?php echo htmlspecialchars($page_desc); ?></p>
            <div class="nav">
                <a class="<?php echo $page=='bananalogs'?'active':''; ?>" href="/bananalogs?limit=<?php echo $limit; ?>">Banana Logs</a>
                <a class="<?php echo $page=='economy'?'active':''; ?>" href="/economy?limit=<?php echo $limit; ?>">Логи экономики</a>
                <a class="<?php echo $page=='botlogs'?'active':''; ?>" href="/botlogs?limit=<?php echo $limit; ?>">Логи бота</a>
                <a class="<?php echo $page=='exchangelogs'?'active':''; ?>" href="/exchangelogs?limit=<?php echo $limit; ?>">Логи обменов</a>
                <a class="<?php echo $page=='requests'?'active':''; ?>" href="/requests?limit=<?php echo $limit; ?>">Формы</a>
            </div>
            <div class="stats">
                <div class="stat"><span>Статус базы</span><b><?php echo $db_connected ? 'Подключена' : 'Не найдена'; ?></b></div>
                <div class="stat"><span>Лимит</span><b><?php echo $limit; ?></b></div>
                <div class="stat"><span>Раздел</span><b><?php echo htmlspecialchars($page_title); ?></b></div>
            </div>
        </div>
        <div class="toolbar">
            <form method="get">
                <input type="number" name="user_id" placeholder="Фильтр по user_id" value="<?php echo $user_id ?? ''; ?>">
                <input type="number" name="limit" placeholder="Лимит" value="<?php echo $limit; ?>">
                <button type="submit">Обновить</button>
            </form>
        </div>
        <div class="panel">
            <h2><?php echo htmlspecialchars($page_title); ?></h2>
            <p style="color:#94a8ba"><?php echo htmlspecialchars($page_desc); ?></p>
            <div>
                <?php if (!empty($rows)): ?>
                    <?php foreach ($rows as $row): ?>
                        <div class="entry">
                            <div>
                                <span class="chip">Запись #<?php echo $row['id'] ?? ''; ?></span>
                                <span class="chip">Дата: <?php echo $row['created_at'] ?? '—'; ?></span>
                            </div>
                            <div class="log-text"><?php echo htmlspecialchars($row['log'] ?? $row['details'] ?? '—'); ?></div>
                        </div>
                    <?php endforeach; ?>
                <?php else: ?>
                    <div class="empty">Записей в этом разделе пока нет.</div>
                <?php endif; ?>
            </div>
        </div>
    </div>
</body>
</html>
