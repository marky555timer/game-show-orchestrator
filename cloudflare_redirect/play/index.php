<?php
// Guest-facing QR target (https://triviacade.com/play/) -- redirects to the
// current tunnel's /play page. play.php does the same, kept for any link
// that names it explicitly.
require __DIR__ . '/_lib.php';
trivia_redirect_to_current('/play');
