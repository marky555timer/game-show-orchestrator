<?php
require __DIR__ . '/config.php';

/**
 * Reads the currently-registered tunnel URL and 302-redirects to
 * "<that url><suffix>", or shows a friendly offline page if there's no
 * URL on file yet, or it's older than STALE_AFTER_SECONDS.
 */
function trivia_redirect_to_current($suffix) {
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

function trivia_show_offline() {
    http_response_code(503);
    header('Content-Type: text/html; charset=utf-8');
    echo <<<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trivia Night</title>
<style>
  body {
    margin: 0; padding: 60px 20px; text-align: center;
    background: #0b0b0d; color: #f2f2f2;
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  }
  h1 { color: #ff3b30; font-size: 1.3rem; }
  p { color: #999; }
</style>
</head>
<body>
  <h1>No show running right now</h1>
  <p>Check back once trivia night starts!</p>
</body>
</html>
HTML;
    exit;
}
