<?php
// Called by the local app's drivers/tunnel_engine.py every time Cloudflare
// Tunnel's public URL is (re)established, plus a periodic heartbeat.
require __DIR__ . '/config.php';

header('Content-Type: application/json');

$raw = file_get_contents('php://input');
$data = json_decode($raw, true);

$secret = $data['secret'] ?? '';
$url = $data['url'] ?? '';

if (!is_string($secret) || !hash_equals(SHARED_SECRET, $secret)) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'bad secret']);
    exit;
}

// Only accept a well-formed https URL -- keeps a leaked/guessed secret
// from being used to redirect guests somewhere arbitrary and non-https.
if (!is_string($url) || !preg_match('#^https://[a-zA-Z0-9.\-]+(:\d+)?(/.*)?$#', $url)) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => 'bad url']);
    exit;
}

$payload = json_encode(['url' => rtrim($url, '/'), 'updated_at' => time()]);
$ok = file_put_contents(STATE_FILE, $payload, LOCK_EX);

if ($ok === false) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'could not write state file -- check directory permissions']);
    exit;
}

echo json_encode(['ok' => true]);
