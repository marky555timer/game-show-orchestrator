<?php
// Guest-facing QR target -- redirects to the current tunnel's /play page.
require __DIR__ . '/_lib.php';
trivia_redirect_to_current('/play');
