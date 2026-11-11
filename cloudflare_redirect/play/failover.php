<?php
// Backup-Pi failover (2026-11-08) -- the other half of the Orchestrator's
// drivers/failover_engine.py. Both show Pis POST here every few seconds:
//
//   {"secret", "action": "report"|"claim", "device", "name", "role",
//    "version", "admin_url", "force", "snapshot"?}
//
// This file remembers which Pi OWNS the show and the owner's latest snapshot
// (show, song, players, scores, round, asked questions). Only the owner's
// snapshot is stored. "claim" makes the caller the owner -- always with
// force (TAKE CONTROL), otherwise only when nobody alive owns it. Every
// reply carries the owner, whether it is still checking in, both Pis, and
// the stored snapshot, with ages worked out on THIS clock (the Pis have no
// real-time clock battery, so their own times can't be trusted).
//
// State lives outside public_html (play_state/failover.json), so a site
// redeploy never wipes it.
require __DIR__ . '/config.php';

header('Content-Type: application/json');
header('Cache-Control: no-store');

define('FAILOVER_FILE', STATE_DIR . '/failover.json');
define('FAILOVER_ALIVE_SECONDS', 30);          // config.FAILOVER_ALIVE_SECONDS on the Pi
define('FAILOVER_FORGET_SECONDS', 7 * 86400);  // drop a Pi not seen for a week
define('FAILOVER_MAX_BODY', 1024 * 1024);

function fail($code, $msg) {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') fail(405, 'POST only');
$raw = file_get_contents('php://input', false, null, 0, FAILOVER_MAX_BODY + 1);
if ($raw === false || strlen($raw) > FAILOVER_MAX_BODY) fail(413, 'too big');
$data = json_decode($raw, true);
if (!is_array($data)) fail(400, 'bad json');

$secret = $data['secret'] ?? '';
if (!is_string($secret) || !hash_equals(SHARED_SECRET_SHA256, hash('sha256', $secret))) fail(403, 'bad secret');

$device = $data['device'] ?? '';
if (!is_string($device) || !preg_match('/^[A-Za-z0-9._-]{1,64}$/', $device)) fail(400, 'bad device');
$action = $data['action'] ?? 'report';
if (!in_array($action, ['report', 'claim'], true)) fail(400, 'bad action');

if (!is_dir(STATE_DIR)) @mkdir(STATE_DIR, 0755, true);
$fp = @fopen(FAILOVER_FILE, 'c+');
if (!$fp) fail(500, 'could not open state file -- check directory permissions');
flock($fp, LOCK_EX);
$st = json_decode(stream_get_contents($fp), true);
if (!is_array($st)) $st = [];
$st += ['owner' => '', 'epoch' => 0, 'owner_since' => 0, 'devices' => [],
        'snapshot' => null, 'snapshot_at' => 0, 'snapshot_from' => ''];
$now = time();

// This Pi checks in.
$dev = $st['devices'][$device] ?? [];
$dev['last_seen'] = $now;
$dev['name'] = substr(is_string($data['name'] ?? null) ? $data['name'] : $device, 0, 40);
$dev['role'] = (($data['role'] ?? '') === 'backup') ? 'backup' : 'primary';
$dev['version'] = substr(is_string($data['version'] ?? null) ? $data['version'] : '', 0, 20);
$admin = $data['admin_url'] ?? '';
if (is_string($admin) && preg_match('#^https://[a-zA-Z0-9.\-]+(:\d+)?/?$#', $admin)) {
    $dev['admin_url'] = rtrim($admin, '/');
} else {
    unset($dev['admin_url']);
}
$st['devices'][$device] = $dev;
foreach ($st['devices'] as $id => $d) {
    if ($now - ($d['last_seen'] ?? 0) > FAILOVER_FORGET_SECONDS) unset($st['devices'][$id]);
}

function owner_alive($st, $now) {
    $o = $st['owner'];
    return $o !== '' && isset($st['devices'][$o])
        && $now - $st['devices'][$o]['last_seen'] <= FAILOVER_ALIVE_SECONDS;
}

// The snapshot as it stood before this request -- a claim hands the new
// owner the old owner's last report, so it knows where to pick up.
$prev_snapshot = $st['snapshot'];
$prev_snapshot_age = $st['snapshot_at'] ? $now - $st['snapshot_at'] : null;

$claimed = null;
if ($action === 'claim') {
    $force = !empty($data['force']);
    if ($st['owner'] === $device) {
        $claimed = true;
    } elseif ($force || !owner_alive($st, $now)) {
        $st['owner'] = $device;
        $st['owner_since'] = $now;
        $st['epoch'] = intval($st['epoch']) + 1;
        $claimed = true;
    } else {
        $claimed = false;
    }
}

// Only the owner's report is kept (and never one from a claim: the new
// owner's first real report follows a few seconds later).
if ($action === 'report' && $st['owner'] === $device && isset($data['snapshot']) && is_array($data['snapshot'])) {
    $st['snapshot'] = $data['snapshot'];
    $st['snapshot_at'] = $now;
    $st['snapshot_from'] = $device;
}

ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode($st));
fflush($fp);
flock($fp, LOCK_UN);
fclose($fp);

$devices = [];
foreach ($st['devices'] as $id => $d) {
    $devices[$id] = ['name' => $d['name'] ?? $id, 'role' => $d['role'] ?? 'primary',
                     'age' => $now - ($d['last_seen'] ?? 0), 'version' => $d['version'] ?? ''];
}

$reply = [
    'ok' => true,
    'owner' => $st['owner'],
    'owner_alive' => owner_alive($st, $now),
    'owner_since_age' => $st['owner_since'] ? $now - $st['owner_since'] : null,
    'epoch' => intval($st['epoch']),
    'devices' => $devices,
];
if ($claimed !== null) {
    $reply['claimed'] = $claimed;
    $reply['snapshot'] = $prev_snapshot;
    $reply['snapshot_age'] = $prev_snapshot_age;
} elseif ($st['owner'] !== $device) {
    // The Pi that isn't in charge reads where the show is.
    $reply['snapshot'] = $st['snapshot'];
    $reply['snapshot_age'] = $st['snapshot_at'] ? $now - $st['snapshot_at'] : null;
}
echo json_encode($reply);
