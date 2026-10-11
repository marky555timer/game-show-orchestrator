<?php
// Operator/admin-facing QR target -- redirects to the current tunnel's
// root (the remote control page).
require __DIR__ . '/_lib.php';
trivia_redirect_to_current('');
