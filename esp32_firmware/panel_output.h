#ifndef PANEL_OUTPUT_H
#define PANEL_OUTPUT_H

#include <FthnLabsDisplay.h>
#include <Adafruit_GFX.h>
#include "config.h"

// The logical, correctly-oriented 64x48 image -- draw into this normally
// (top-left origin, no need to think about chain order/rotation).
extern GFXcanvas1 canvas;

// The physical panel driver.
extern FthnLabsDisplay display;

// Copies `canvas` into the physical panel order + per-panel rotation this
// rig's daisy chain and mounting require. See panel_output.cpp for the
// derivation -- confirmed against hardware with QuadrantTest.ino (v3).
void blitToPanels();

#endif
