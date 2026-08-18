#ifndef BOOT_STATUS_H
#define BOOT_STATUS_H

#include "link_state.h"

// Renders a short status message (plus a small blinking "still alive"
// marker so a long wait doesn't read as hung) through the normal panel
// pipeline -- so startup/reconnect feedback goes through the same
// verified panel order/rotation as real show content, on real hardware,
// not a separate code path that could silently drift out of sync with it.
// Not meant to be called for LinkState::LIVE -- Display.ino stops calling
// this once real frames are flowing.
void renderBootStatus(LinkState state, unsigned long nowMs);

#endif
