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
  // hand.joints["index-finger-tip"] is the wrong signal: three.js creates
  // that entry the first time a joint pose ever arrives and never removes
  // it, so once hand tracking has been seen even once in a session the key
  // stays forever — permanently reading as "tracked" and blocking the
  // controller's own select events for the rest of the session. hand.visible
  // is reset every frame (true only while the input source currently reports
  // .hand), so it actually reflects whether a hand is tracked right now.
  return !hand?.visible;
}
