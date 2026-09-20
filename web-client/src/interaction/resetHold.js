/**
 * Hold-to-reset on a controller face button (pure logic, no Three.js).
 *
 * WebXR only raises events for the trigger (`select`) and the grip
 * (`squeeze`); X/Y/A/B and the thumbstick exist solely as `gamepad.buttons`
 * on the input source, so they have to be polled once per frame and
 * edge-detected here.
 *
 * Reset wipes every model and panel, so a tap must not fire it: the button
 * has to stay down for RESET_HOLD_MS, and the caller draws `progress` as a
 * filling cue so the user can back out by letting go. The latch also refuses
 * to fire twice for one press — without that, holding past the threshold
 * would re-reset on every subsequent frame.
 */

/** xr-standard gamepad button indices (Quest Touch and friends). */
export const XR_BUTTON = {
  TRIGGER: 0,
  SQUEEZE: 1,
  THUMBSTICK: 3,
  // Left controller: X / Y. Right controller: A / B.
  X_OR_A: 4,
  Y_OR_B: 5,
};

export const RESET_HOLD_MS = 1000;

/**
 * Read one button off the input source with the given handedness.
 *
 * Handedness rather than the `getController(i)` index: the index is just the
 * slot an input source happened to arrive in, and on a Quest it flips often
 * enough (controller sleeps, hand tracking hands over, one controller
 * reconnects) that an index-keyed reset would land on the wrong hand.
 */
export function buttonPressed(session, handedness, index) {
  const sources = session?.inputSources;
  if (!sources) return false;
  for (const source of sources) {
    if (source.handedness !== handedness) continue;
    const button = source.gamepad?.buttons?.[index];
    if (button?.pressed) return true;
  }
  return false;
}

/**
 * Hold latch. Feed it the button state once per frame; it answers with how
 * far through the hold the user is and fires exactly once per press.
 *
 * @returns {{ update: (pressed: boolean, nowMs: number) => { active: boolean, progress: number, fired: boolean }, cancel: () => void }}
 */
export function createHoldLatch({ holdMs = RESET_HOLD_MS } = {}) {
  let startedAt = null;
  let consumed = false;

  function cancel() {
    startedAt = null;
    consumed = false;
  }

  function update(pressed, nowMs) {
    if (!pressed) {
      cancel();
      return { active: false, progress: 0, fired: false };
    }
    if (startedAt === null) startedAt = nowMs;

    // A clock that jumps backwards (or a latch fed a stale timestamp) would
    // otherwise park progress at 0 forever and the hold could never complete.
    if (nowMs < startedAt) startedAt = nowMs;

    const progress = holdMs > 0 ? Math.min((nowMs - startedAt) / holdMs, 1) : 1;
    if (progress >= 1 && !consumed) {
      consumed = true;
      return { active: true, progress: 1, fired: true };
    }
    return { active: true, progress, fired: false };
  }

  return { update, cancel };
}
