// Backup-Pi failover panel (2026-11-08, drivers/failover_engine.py).
// Shared by the Remote (index.html) and Setup (setup.html) pages: fills
// <section id="failover-panel"> with who has the show, and -- on the Pi that
// doesn't -- where the other Pi is in the show plus TAKE CONTROL.
(function () {
  const box = document.getElementById('failover-panel');
  if (!box) return;
  let armedUntil = 0;
  let busy = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  }
  function mmss(t) {
    t = Math.max(0, Math.round(t || 0));
    return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0');
  }
  function ago(s) {
    if (s == null) return 'never';
    s = Math.round(s);
    return s < 90 ? s + 's ago' : Math.round(s / 60) + ' min ago';
  }
  const PHASES = {setup: 'at the show menu', countdown: 'counting down to the start', dark: 'holding BLACK',
                  intro: 'playing the intro', live: 'live', outro: 'in the outro'};

  async function refresh() {
    let d = null;
    try {
      const r = await fetch('/api/failover/status');
      d = await r.json();
    } catch (e) { return; }
    if (!d || !d.enabled) { box.style.display = 'none'; return; }
    box.style.display = '';
    render(d);
  }

  function render(d) {
    const other = d.other;
    const otherName = other ? esc(other.name) : 'The other Pi';
    const otherLine = other
      ? `${otherName}: ${other.age <= 30 ? 'online' : '<b style="color:#ff6b6b">offline</b>'} (checked in ${ago(other.age)})`
      : 'No other Pi has checked in yet.';
    let html = '<h2 style="margin-top:0">Failover</h2>';
    if (!d.reachable) {
      html += `<div style="color:#ffb21e">Can't reach triviacade.com${d.error ? ' (' + esc(d.error) + ')' : ''} &mdash; failover status unknown. This Pi carries on as it is.</div>`;
    }
    if (d.i_own) {
      html += `<div style="color:#5be37d;font-weight:700">This Pi (${esc(d.me.name)}) has the show.</div>`;
      html += `<div style="font-size:.85rem;color:#bbb;margin-top:4px">${otherLine}</div>`;
      html += `<div style="font-size:.8rem;color:#888;margin-top:4px">Backup controls: <a style="color:#46e6ff" href="${esc(d.backup_admin_url)}">${esc(d.backup_admin_url)}</a></div>`;
    } else {
      const owner = d.owner ? esc(d.owner_name || 'The other Pi') : '';
      const p = d.position;
      if (owner && d.owner_alive) {
        html += `<div style="font-weight:700">${owner} is running the show.</div>`;
      } else if (owner) {
        html += `<div style="font-weight:700;color:#ff6b6b">${owner} has gone quiet &mdash; it may be down.</div>`;
      } else {
        html += '<div style="font-weight:700">No Pi has the show right now.</div>';
      }
      if (p) {
        const stale = p.age != null && p.age > 20;
        html += '<div style="margin-top:6px;font-size:.9rem;line-height:1.45">';
        if (p.show) {
          html += `Show <b>${esc(p.show)}</b> &mdash; ${esc(PHASES[p.phase] || p.phase)}${p.background ? ' (after-show music)' : ''}${p.medley ? ' (Dance Medley)' : ''}<br>`;
        } else {
          html += `${esc(PHASES[p.phase] || p.phase)}<br>`;
        }
        if (p.phase === 'live' && p.title) {
          const num = p.song_count ? `Song ${p.song_number || '?'} of ${p.song_count}: ` : '';
          html += `${num}<b>${esc(p.title)}</b>${p.artist ? ' &ndash; ' + esc(p.artist) : ''} (${mmss(p.elapsed)}${p.duration ? ' / ' + mmss(p.duration) : ''})<br>`;
          html += p.next_title ? `Next: ${esc(p.next_title)}${p.next_artist ? ' &ndash; ' + esc(p.next_artist) : ''}<br>`
                               : (p.mode === 'play' ? 'Next: end of the show<br>' : 'Next: picked live<br>');
        }
        html += `${p.players} player${p.players === 1 ? '' : 's'}, round ${p.round}${p.leader ? ', leading: ' + esc(p.leader) : ''}<br>`;
        html += `<span style="color:${stale ? '#ff6b6b' : '#888'};font-size:.8rem">Last report ${ago(p.age)}</span>`;
        html += '</div>';
      }
      const armed = Date.now() < armedUntil;
      html += `<button class="btn big" id="failover-take-btn" style="margin-top:10px;width:100%;font-weight:700;${armed
        ? 'background:#ff3b30;border-color:#ff3b30;color:#fff' : 'background:#4a0f0f;border-color:#ff3b30;color:#ffb6b6'}"
        ${d.taking || busy ? 'disabled' : ''}>${d.taking || busy ? 'TAKING CONTROL...' : armed ? 'TAP AGAIN TO TAKE OVER' : 'TAKE CONTROL'}</button>`;
      html += `<div style="font-size:.78rem;color:#888;margin-top:4px">Runs the show on this Pi (${esc(d.me.name)}) from the next song, with the same players and scores. The other Pi stops.</div>`;
    }
    if (d.message) html += `<div style="margin-top:8px;font-size:.85rem;color:#ffd27a">${esc(d.message)}</div>`;
    box.innerHTML = html;
    const btn = document.getElementById('failover-take-btn');
    if (btn) btn.onclick = take;
  }

  async function take() {
    if (Date.now() >= armedUntil) {
      armedUntil = Date.now() + 4000;
      refresh();
      setTimeout(refresh, 4100);
      return;
    }
    armedUntil = 0;
    busy = true;
    refresh();
    try {
      await fetch('/api/failover/take', {method: 'POST'});
    } catch (e) {}
    setTimeout(() => { busy = false; refresh(); }, 2500);
  }

  refresh();
  setInterval(refresh, 3000);
})();
