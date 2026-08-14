# Trivia Night static QR redirector

Deploys to your own static/PHP-capable web server. Its job: give the show's
QR codes a URL that **never changes**, even though the local show machine's
actual public address (a Cloudflare Tunnel quick-tunnel URL) changes every
time the app restarts.

```
Guest scans QR  --->  https://yourdomain.com/trivia/play.php   (this directory, never changes)
                                    |
                                    | 302 redirect to whatever's on file
                                    v
                       https://xxxx.trycloudflare.com/play     (changes every app restart)
                                    |
                                    v
                       your show machine's local web remote
```

The local app (`drivers/tunnel_engine.py`) POSTs its current tunnel URL to
`update.php` here every time it changes, plus a heartbeat every 60 seconds
while it's running.

## What's in here

- `play.php` — **guest-facing QR target.** Redirects to `<tunnel>/play`.
- `admin.php` — **operator-facing QR target.** Redirects to `<tunnel>/`.
- `update.php` — the local app POSTs here to report its current tunnel URL.
- `config.php` — the shared secret + staleness settings.
- `_lib.php` — shared redirect/offline-page logic (not called directly).
- `.htaccess` — Apache hardening (blocks direct access to the state file
  and config; harmless no-op if you're not on Apache).

## Deploy steps

1. **Upload this whole folder** to your web server, anywhere you like —
   e.g. `https://yourdomain.com/trivia/`. It needs PHP support; no
   database, no Composer, no build step.
2. **Make the directory writable** by the PHP process — `update.php`
   writes `current_url.json` here to remember the current tunnel address.
   On most shared hosts, the default permissions already allow this; if
   `update.php` returns a "could not write state file" error, `chmod 755`
   (or `775`) the directory.
3. **The shared secret is already pre-filled** to match
   `config.TUNNEL_REDIRECT_SECRET` in the local app's `config.py` — you
   don't need to change anything unless you want to rotate it (if you do,
   change it in *both* places).
4. **Tell me (or fill in yourself) the three resulting URLs** in the local
   app's `config.py`:
   ```python
   TUNNEL_REDIRECT_ADMIN_URL = "https://yourdomain.com/trivia/admin.php"
   TUNNEL_REDIRECT_PLAY_URL = "https://yourdomain.com/trivia/play.php"
   TUNNEL_REDIRECT_UPDATE_URL = "https://yourdomain.com/trivia/update.php"
   ```
   Until these are filled in, the app's QR codes fall back to the old
   LAN-IP behavior automatically — nothing breaks in the meantime.
5. **Install `cloudflared`** on the show machine if it isn't already
   (free, no account needed for this):
   <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>
   The app detects it automatically at startup — no further config.

## Testing it

- With the app running and `cloudflared` installed, watch the console for
  `[TUNNEL] Public URL: https://....trycloudflare.com`.
- Visit `https://yourdomain.com/trivia/play.php` in a browser — it should
  302 to `https://xxxx.trycloudflare.com/play`.
- Stop the app; wait ~3 minutes (`STALE_AFTER_SECONDS` in `config.php`);
  reload the same URL — it should now show the "No show running right
  now" page instead of redirecting to a dead link.

## nginx equivalent of the `.htaccess` rules

If your host runs nginx instead of Apache, add this to your server block
instead (the `.htaccess` file itself is ignored by nginx):

```nginx
location ~ /trivia/(current_url\.json|config\.php)$ {
    deny all;
}
```
