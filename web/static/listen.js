// "Stream the show's audio here" (2026-11-25): one checkbox shared by the admin pages (Setup, Countdown,
// Remote). Ticked on one page, it stays on as the host moves between them (localStorage tc_listen) --
// the stream picks up again on the next page. Unticking anywhere stops it in every open admin tab.
// The stream is /api/audio/stream (the phones' "Listen to the show"), a few seconds behind the room.
// A page put here as <div id="listen-slot"></div> gets the checkbox there; else it goes at the bottom.
(function () {
  const KEY = 'tc_listen';
  const slot = document.getElementById('listen-slot') || (() => {
    const d = document.createElement('div');
    d.id = 'listen-slot';
    d.style.cssText = 'margin:16px auto;max-width:640px;padding:0 12px';
    document.body.appendChild(d);
    return d;
  })();
  slot.innerHTML =
    '<label style="display:flex;align-items:center;gap:12px;font-size:1rem;cursor:pointer;padding:10px 12px;' +
    'background:#161619;border:1px solid #2a2a2e;border-radius:10px;color:#f2f2f2">' +
    '<input type="checkbox" id="tab-listen" style="width:26px;height:26px;accent-color:#ffd400;flex-shrink:0">' +
    '<span>Stream the show\'s audio here <span id="tab-listen-msg" style="color:#888;font-size:.8rem"></span></span></label>' +
    '<audio id="tab-listen-audio" preload="none"></audio>';
  const box = document.getElementById('tab-listen');
  const au = document.getElementById('tab-listen-audio');
  const msg = (t) => { document.getElementById('tab-listen-msg').textContent = t; };
  let waitingTap = null;

  function stop() {
    au.pause(); au.removeAttribute('src'); au.load();
    if (waitingTap) { document.removeEventListener('pointerdown', waitingTap, true); waitingTap = null; }
  }
  function start() {
    msg('connecting...');
    au.src = '/api/audio/stream?t=' + Date.now();
    au.play().then(() => msg('playing (a few seconds behind the room)')).catch(() => {
      // A new page may not play sound until it is touched (browser rule): resume on the first tap.
      msg('tap anywhere on the page to resume');
      if (!waitingTap) {
        waitingTap = () => {
          document.removeEventListener('pointerdown', waitingTap, true); waitingTap = null;
          if (box.checked) start();
        };
        document.addEventListener('pointerdown', waitingTap, true);
      }
    });
  }
  box.addEventListener('change', () => {
    try { localStorage.setItem(KEY, box.checked ? '1' : '0'); } catch (e) {}
    if (box.checked) start(); else { stop(); msg(''); }
  });
  au.addEventListener('error', () => {
    if (!box.checked || !au.getAttribute('src')) return;
    stop();
    msg('stream dropped -- retrying...');
    setTimeout(() => { if (box.checked) start(); }, 3000);
  });
  window.addEventListener('storage', (e) => {      // unticked in another admin tab: stop here too
    if (e.key === KEY && e.newValue !== '1' && box.checked) { box.checked = false; stop(); msg(''); }
  });
  let on = false;
  try { on = localStorage.getItem(KEY) === '1'; } catch (e) {}
  if (on) { box.checked = true; start(); }
})();
