<?php
// Trivia Night dynamic-tunnel redirector -- config.
//
// This directory redirects the app's QR codes (which encode a URL on
// THIS server, so they never have to change) to wherever the local
// show machine's Cloudflare Tunnel currently is (which DOES change every
// time it restarts). See ../drivers/tunnel_engine.py for the other half.

// SHA-256 of config.TUNNEL_REDIRECT_SECRET in the local app's config.py.
// Only the hash lives here, because this folder is deployed from the
// triviacade-site git repo. If you ever change the secret, recompute:
//   python -c "import hashlib,config;print(hashlib.sha256(config.TUNNEL_REDIRECT_SECRET.encode()).hexdigest())"
define('SHARED_SECRET_SHA256', '297293434ae9acc9b869b54d586a0b99bbf317a23f2d8a41d0098c19e0927d87');

// Where the current tunnel URL is persisted between requests. Kept OUTSIDE
// public_html (domains/triviacade.com/play_state/) so a git redeploy of the
// site -- which replaces public_html wholesale -- never wipes it.
define('STATE_DIR', dirname(__DIR__, 2) . '/play_state');
define('STATE_FILE', STATE_DIR . '/current_url.json');

// If the local app hasn't checked in within this many seconds, treat the
// tunnel as dead and show a friendly "no show right now" page instead of
// redirecting guests into a broken link. drivers/tunnel_engine.py
// heartbeats every config.TUNNEL_HEARTBEAT_SECONDS (60s by default), so
// this should stay comfortably above that.
define('STALE_AFTER_SECONDS', 180);
