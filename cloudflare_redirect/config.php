<?php
// Trivia Night dynamic-tunnel redirector -- config.
//
// This directory redirects the app's QR codes (which encode a URL on
// THIS server, so they never have to change) to wherever the local
// show machine's Cloudflare Tunnel currently is (which DOES change every
// time it restarts). See ../drivers/tunnel_engine.py for the other half.

// Must match config.TUNNEL_REDIRECT_SECRET exactly in the local app's
// config.py -- pre-filled to match out of the box. If you ever change
// this, change it in BOTH places together.
define('SHARED_SECRET', 'm8YaRwCJ_cmRybVu0VGhNbKuvyU9T_xqw4c79ZjXQlQ');

// Where the current tunnel URL is persisted between requests -- the web
// server process (PHP) must have write access to this directory.
define('STATE_FILE', __DIR__ . '/current_url.json');

// If the local app hasn't checked in within this many seconds, treat the
// tunnel as dead and show a friendly "no show right now" page instead of
// redirecting guests into a broken link. drivers/tunnel_engine.py
// heartbeats every config.TUNNEL_HEARTBEAT_SECONDS (60s by default), so
// this should stay comfortably above that.
define('STALE_AFTER_SECONDS', 180);
