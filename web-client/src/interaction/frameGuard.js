/**
 * Keeps one bad frame from ending the session.
 *
 * three.js re-queues the next frame *after* the animation callback returns:
 *
 *   function onAnimationFrame(time, frame) {
 *     animationLoop(time, frame);                        // throws here...
 *     requestId = context.requestAnimationFrame(...);    // ...never runs
 *   }
 *
 * So a single uncaught throw anywhere in the loop stops rendering permanently
 * — in a headset that is a frozen view until the session is reloaded. Wrapping
 * each step means a throw costs one step of one frame instead of the session.
 *
 * Logging is throttled per step: at 90 fps an unthrottled error would emit 90
 * lines a second and bury the very message you need to read.
 */

export function createFrameGuard({
  log = (msg) => console.error(msg),
  now = () => Date.now(),
  throttleMs = 5000,
} = {}) {
  const lastLoggedAt = new Map();
  const failureCounts = new Map();

  /**
   * Run one step. Returns true if it completed, false if it threw.
   * Never rethrows — that is the entire point.
   */
  function guard(label, fn) {
    try {
      fn();
      return true;
    } catch (err) {
      const count = (failureCounts.get(label) || 0) + 1;
      failureCounts.set(label, count);

      const t = now();
      const last = lastLoggedAt.get(label);
      if (last === undefined || t - last >= throttleMs) {
        lastLoggedAt.set(label, t);
        const detail = err && err.message ? err.message : String(err);
        log(`[frame] ${label} threw (${count}x): ${detail}`);
      }
      return false;
    }
  }

  /** How many times a step has thrown this session. */
  guard.failures = (label) => failureCounts.get(label) || 0;

  return guard;
}
