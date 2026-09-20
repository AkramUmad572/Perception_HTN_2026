/**
 * Which input source is allowed to drive left-hand push-to-talk.
 *
 * WebXR delivers a hand-tracking pinch as a `select` event on the very same
 * controller object that a physical trigger fires on. main.js also samples
 * the thumb/index gap every frame in pollHand and latches it with hysteresis.
 * Honouring both on one hand gives push-to-talk two independent drivers
 * running off different thresholds, and the runtime's has no hysteresis at
 * all — a single frame of finger jitter or occlusion emits a full
 * selectstart, which barges in on whatever Percy is saying and drops the
 * reply audio mid-sentence.
 *
 * So: a tracked hand is pollHand's to arbitrate, and the select events are
 * left to actual controllers.
 */
export function selectEventDrivesPtt(hand) {
  return !hand?.joints?.["index-finger-tip"];
}
