<?php
// Backup-Pi failover (2026-11-08): opens the admin remote of the show Pi that
// is NOT running the show right now (normally the backup), so the operator
// can see where the show is and press TAKE CONTROL. Same login hand-off as
// admin.php. The Pis register their addresses through failover.php.
require_once __DIR__ . '/_lib.php';
require_once __DIR__ . '/designer/_d.php';

header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

function standby_url() {
    $file = STATE_DIR . '/failover.json';
    $st = file_exists($file) ? json_decode(file_get_contents($file), true) : null;
    if (!is_array($st) || empty($st['devices'])) return '';
    $best = '';
    $best_seen = 0;
    foreach ($st['devices'] as $id => $d) {
        if ($id === ($st['owner'] ?? '') || empty($d['admin_url'])) continue;
        $seen = $d['last_seen'] ?? 0;
        if (time() - $seen > 60) continue;
        if ($seen > $best_seen) { $best = $d['admin_url']; $best_seen = $seen; }
    }
    return $best;
}

$url = standby_url();
if ($url === '') {
    http_response_code(503);
    header('Content-Type: text/html; charset=utf-8');
    echo <<<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Triviacade backup</title>
<style>
  body { margin: 0; padding: 60px 20px; text-align: center; background: #130F2A; color: #F1EEFF;
         font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
  h1 { color: #FFB21E; font-size: 1.3rem; }
  p { color: #B9B2DE; }
  a { color: #46E6FF; }
</style>
</head>
<body>
  <h1>No standby Pi is online</h1>
  <p>The backup Pi hasn't checked in during the last minute (or it's the one running the show).</p>
  <p><a href="/play/admin.php">Main remote</a></p>
</body>
</html>
HTML;
    exit;
}
if (!password_set()) {
    header('Location: ' . $url . '/', true, 302);
    exit;
}
if (!logged_in()) {
    header('Location: /play/designer/?next=backup', true, 302);
    exit;
}
header('Location: ' . $url . '/auth/handoff?code=' . new_handoff_code() . '&next=' . urlencode('/'), true, 302);
exit;
