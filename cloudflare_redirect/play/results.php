<?php
// Frozen end-of-night results (2026-11-16), so a player's link works after the
// show machine is off and its tunnel address has changed.
//
//   POST  (the show machine, drivers/results_archive.py)
//         {"secret": ..., "results": {session, ended_at, questions, players}}
//         -> stored in play_state/results/<session>.json (outside public_html,
//            so a site redeploy never wipes it). Files older than 30 days go.
//   GET   ?r=<session>[&p=<player id>]            -> the results page
//   GET   ?r=<session>[&p=<player id>]&json=1     -> its data
//         The board is public (team names, emoji, totals). A team's own
//         answers are only returned when its 32-hex player id is supplied.
require __DIR__ . '/config.php';

define('RESULTS_DIR', STATE_DIR . '/results');
define('RESULTS_KEEP_DAYS', 30);
define('RESULTS_MAX_BYTES', 400000);

function res_id_ok($s) { return is_string($s) && preg_match('/^[a-z0-9]{6,32}$/', $s) === 1; }
function res_pid_ok($s) { return is_string($s) && preg_match('/^[0-9a-f]{32}$/', $s) === 1; }

header('X-Robots-Tag: noindex, nofollow');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    $raw = file_get_contents('php://input', false, null, 0, RESULTS_MAX_BYTES + 1);
    if ($raw === false || strlen($raw) > RESULTS_MAX_BYTES) {
        http_response_code(413);
        echo json_encode(['ok' => false, 'error' => 'too large']);
        exit;
    }
    $data = json_decode($raw, true);
    $secret = is_array($data) ? ($data['secret'] ?? '') : '';
    if (!is_string($secret) || !hash_equals(SHARED_SECRET_SHA256, hash('sha256', $secret))) {
        http_response_code(403);
        echo json_encode(['ok' => false, 'error' => 'bad secret']);
        exit;
    }
    $res = $data['results'] ?? null;
    if (!is_array($res) || !res_id_ok($res['session'] ?? null)
        || !is_array($res['players'] ?? null) || !is_array($res['questions'] ?? null)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'bad results']);
        exit;
    }
    if (!is_dir(RESULTS_DIR)) @mkdir(RESULTS_DIR, 0755, true);
    $path = RESULTS_DIR . '/' . $res['session'] . '.json';
    $ok = file_put_contents($path, json_encode($res, JSON_UNESCAPED_UNICODE), LOCK_EX);
    if ($ok === false) {
        http_response_code(500);
        echo json_encode(['ok' => false, 'error' => 'could not write']);
        exit;
    }
    $cutoff = time() - RESULTS_KEEP_DAYS * 86400;
    foreach (glob(RESULTS_DIR . '/*.json') ?: [] as $f) {
        if (@filemtime($f) < $cutoff) @unlink($f);
    }
    echo json_encode(['ok' => true]);
    exit;
}

$sid = $_GET['r'] ?? '';
$pid = $_GET['p'] ?? '';

if (isset($_GET['json'])) {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    $file = res_id_ok($sid) ? RESULTS_DIR . '/' . $sid . '.json' : '';
    $data = ($file !== '' && is_file($file)) ? json_decode(file_get_contents($file), true) : null;
    if (!is_array($data) || !is_array($data['players'] ?? null)) {
        http_response_code(404);
        echo json_encode(['ok' => false]);
        exit;
    }
    $players = $data['players'];
    uasort($players, function ($a, $b) { return ($a['rank'] ?? 0) <=> ($b['rank'] ?? 0); });
    $board = [];
    foreach ($players as $id => $p) {
        $board[] = ['rank' => $p['rank'] ?? 0, 'name' => (string)($p['name'] ?? ''), 'emoji' => (string)($p['emoji'] ?? ''),
                    'score' => (int)($p['score'] ?? 0), 'me' => $id === $pid];
        if (count($board) >= 30) break;
    }
    $out = ['ok' => true, 'ended_at' => $data['ended_at'] ?? 0, 'teams' => count($players),
            'asked' => count($data['questions']), 'board' => $board, 'mine' => null];
    if (res_pid_ok($pid) && isset($players[$pid])) {
        $me = $players[$pid];
        $answers = [];
        $right = 0;
        foreach ($data['questions'] as $q) {
            $ch = $q['choices'] ?? [];
            $ci = $q['correct_index'] ?? -1;
            $pick = $me['picks'][(string)($q['n'] ?? '')] ?? null;
            $isRight = $pick !== null && $pick === $ci;
            if ($isRight) $right++;
            $answers[] = [
                'n' => $q['n'] ?? 0, 'question' => (string)($q['question'] ?? ''), 'category' => (string)($q['category'] ?? ''),
                'correct' => $ch[$ci] ?? '', 'yours' => ($pick !== null ? ($ch[$pick] ?? null) : null), 'right' => $isRight,
            ];
        }
        $asked = count($answers);
        $out['mine'] = ['rank' => $me['rank'] ?? 0, 'score' => (int)($me['score'] ?? 0),
                        'name' => (string)($me['name'] ?? ''), 'emoji' => (string)($me['emoji'] ?? ''),
                        'asked' => $asked, 'correct' => $right,
                        'percent' => $asked ? (int)round(100 * $right / $asked) : 0,
                        'best_streak' => (int)($me['best_streak'] ?? 0), 'answers' => $answers];
    }
    echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP);
    exit;
}

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-cache');
echo <<<'HTML'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Triviacade Results</title>
<style>
  :root { color-scheme: dark; --ink:#130F2A; --stage-inset:#241D52; --rail:#3A3080; --text:#F1EEFF; --muted:#B9B2DE; --faint:#7D75B0; --amber:#FFB21E; --pink:#FF4D94; --green:#5BE38A; }
  * { box-sizing: border-box; }
  body { margin:0; padding:18px 16px 50px; background:var(--ink); color:var(--text); font-family:-apple-system,"Segoe UI",Roboto,sans-serif; text-align:center; }
  .wrap { max-width: 460px; margin: 0 auto; }
  .brand { color:var(--amber); font-weight:800; letter-spacing:.18em; font-size:1.1rem; margin:6px 0 2px; }
  .sub { color:var(--muted); font-size:.8rem; letter-spacing:.3em; margin-bottom:14px; }
  h1 { color:var(--amber); font-size:1.7rem; margin:8px 0 4px; text-shadow:0 0 14px rgba(255,178,30,.5); }
  .big { font-size:1.05rem; font-weight:700; margin-top:6px; }
  .small { color:var(--muted); font-size:.85rem; margin:3px 0 10px; }
  .btn { display:inline-block; font:inherit; font-weight:700; color:var(--ink); background:var(--text); border:0; border-radius:12px; padding:12px 22px; margin:6px 4px; text-decoration:none; cursor:pointer; }
  .btn.ghost { background:var(--stage-inset); color:var(--text); border:1px solid var(--rail); }
  .row { display:flex; align-items:center; gap:10px; text-align:left; margin:6px 0; padding:6px 10px 6px 6px; border-radius:8px;
         background:linear-gradient(90deg, rgba(255,178,30,.16) var(--bar,0%), transparent var(--bar,0%)); }
  .row.me { outline:2px solid var(--pink); }
  .rank { width:26px; text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
  .em { width:30px; height:30px; border-radius:50%; background:rgba(255,255,255,.07); box-shadow:0 0 0 2px var(--amber); display:inline-flex; align-items:center; justify-content:center; font-size:18px; flex-shrink:0; }
  .nm { flex:1; overflow-wrap:anywhere; }
  .pts { font-variant-numeric:tabular-nums; color:var(--muted); }
  #answers { text-align:left; margin-top:8px; }
  .ans { padding:10px 0; border-bottom:1px solid var(--rail); font-size:.92rem; }
  .ans .q { font-weight:700; margin-bottom:4px; } .ans .a { color:var(--muted); }
  .ans.right .mark { color:var(--green); } .ans.wrong .mark { color:var(--pink); }
  .cat { font-size:.7rem; letter-spacing:.06em; text-transform:uppercase; color:var(--amber); }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">TRIVIACADE</div><div class="sub">MUSIC &middot; TRIVIA</div>
  <div id="out"><div class="small">Loading&hellip;</div></div>
</div>
<script>
(function () {
  var qs = new URLSearchParams(location.search);
  var r = qs.get('r') || '', p = qs.get('p') || '';
  var out = document.getElementById('out');
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  fetch('results.php?json=1&r=' + encodeURIComponent(r) + '&p=' + encodeURIComponent(p), {cache: 'no-store'})
    .then(function (x) { return x.json(); })
    .then(render)
    .catch(function () { render({ok: false}); });
  function render(v) {
    out.textContent = '';
    if (!v || !v.ok) {
      out.appendChild(el('h1', '', 'Results not found'));
      out.appendChild(el('div', 'small', 'This link may have expired (results are kept for 30 days).'));
      return;
    }
    var m = v.mine;
    out.appendChild(el('h1', '', 'Final Scores'));
    if (v.ended_at) out.appendChild(el('div', 'small', new Date(v.ended_at * 1000).toLocaleDateString(undefined, {weekday: 'long', month: 'short', day: 'numeric'})));
    if (m) {
      out.appendChild(el('div', 'big', "You've got " + m.correct + ' / ' + m.asked + ' correct (' + m.percent + '%)'));
      out.appendChild(el('div', 'small', 'Rank ' + m.rank + ' / ' + v.teams + (m.best_streak >= 3 ? ' · best streak ' + m.best_streak : '')));
      var vb = el('button', 'btn', 'View answers');
      vb.onclick = function () { ans.classList.toggle('hidden'); };
      out.appendChild(vb);
    } else {
      out.appendChild(el('div', 'small', v.teams + ' teams · ' + v.asked + ' questions'));
    }
    var top = Math.max(1, v.board.length ? v.board[0].score : 1);
    v.board.forEach(function (b) {
      var row = el('div', 'row' + (b.me ? ' me' : ''));
      row.style.setProperty('--bar', Math.round(100 * b.score / top) + '%');
      row.appendChild(el('span', 'rank', b.rank + '.'));
      row.appendChild(el('span', 'em', b.emoji));
      row.appendChild(el('span', 'nm', b.name));
      row.appendChild(el('span', 'pts', b.score + 'p'));
      out.appendChild(row);
    });
    var ans = el('div', 'hidden'); ans.id = 'answers';
    if (m) {
      m.answers.slice().reverse().forEach(function (a) {
        var d = el('div', 'ans ' + (a.right ? 'right' : 'wrong'));
        if (a.category) d.appendChild(el('div', 'cat', a.category));
        var q = el('div', 'q'); q.appendChild(el('span', 'mark', a.right ? '✓ ' : '✗ ')); q.appendChild(document.createTextNode(a.n + '. ' + a.question)); d.appendChild(q);
        var t = a.yours === null ? 'No answer' : 'You: ' + a.yours;
        if (!a.right) t += ' · Answer: ' + a.correct;
        d.appendChild(el('div', 'a', t));
        ans.appendChild(d);
      });
      out.appendChild(ans);
    }
    out.appendChild(el('div', 'small', 'Thank you for playing! ❤️'));
    var home = el('a', 'btn ghost', 'Play at the next show'); home.href = '/play/';
    out.appendChild(home);
  }
})();
</script>
</body>
</html>
HTML;
