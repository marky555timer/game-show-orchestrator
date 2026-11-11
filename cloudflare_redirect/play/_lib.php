<?php
require __DIR__ . '/config.php';

/**
 * Reads the currently-registered tunnel URL and 302-redirects to
 * "<that url><suffix>", or shows a friendly offline page if there's no
 * URL on file yet, or it's older than STALE_AFTER_SECONDS.
 */
function trivia_redirect_to_current($suffix) {
    // The tunnel URL changes on every show-machine restart: never let a
    // browser or the host's CDN (Hostinger hcdn) cache this answer.
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    if (!file_exists(STATE_FILE)) {
        trivia_show_offline();
    }
    $raw = file_get_contents(STATE_FILE);
    $data = json_decode($raw, true);
    if (!$data || !isset($data['url'], $data['updated_at'])) {
        trivia_show_offline();
    }
    if (time() - $data['updated_at'] > STALE_AFTER_SECONDS) {
        trivia_show_offline();
    }
    header('Location: ' . $data['url'] . $suffix, true, 302);
    exit;
}

/**
 * Backup-Pi failover (2026-11-08): a player's phone that lost the show comes
 * back as /play/?pid=<id> (web/static/play.html) -- pass the id on so the Pi
 * now running the show recognises the player.
 */
function trivia_pid_query() {
    $pid = $_GET['pid'] ?? '';
    return (is_string($pid) && preg_match('/^[0-9a-f]{32}$/', $pid)) ? '?pid=' . $pid : '';
}

function trivia_show_offline() {
    http_response_code(503);
    header('Content-Type: text/html; charset=utf-8');
    if (trivia_pid_query() !== '') {
        // A player mid-show: the backup Pi may be taking over -- keep trying.
        echo <<<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Triviacade</title>
<style>
  body {
    margin: 0; padding: 60px 20px; text-align: center;
    background: #130F2A; color: #F1EEFF;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  }
  h1 { color: #FFB21E; font-size: 1.3rem; }
  p { color: #B9B2DE; }
</style>
</head>
<body>
  <h1>Reconnecting to the show&hellip;</h1>
  <p>Hang tight &mdash; your score is safe. This page keeps trying by itself.</p>
</body>
</html>
HTML;
        exit;
    }
    echo <<<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Triviacade</title>
<style>
  body {
    margin: 0; padding: 60px 20px; text-align: center;
    background: #130F2A; color: #F1EEFF;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  }
  h1 { color: #FFB21E; font-size: 1.3rem; }
  p { color: #B9B2DE; }
  a { color: #46E6FF; }
</style>
</head>
<body>
  <h1>No show running right now</h1>
  <p>Check back once the game starts!</p>
  <p><a href="/">triviacade.com</a></p>
</body>
</html>
HTML;
    exit;
}
