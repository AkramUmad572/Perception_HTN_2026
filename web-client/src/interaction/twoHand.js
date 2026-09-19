/**
 * Two-hand navigation math. Pure, no Three.js import; vectors are {x, y, z}.
 *
 * Called once per frame with the previous and current pinch points of both
 * hands. The result is an incremental transform: multiply the display zoom by
 * `scale`, rotate by `yaw` about world Y, and move with the hands' midpoint.
 * The zoom is visual only; nothing about it is sent to the server.
 */

export const NAV_SCALE_MIN = 0.1;
export const NAV_SCALE_MAX = 10;

const EPS = 1e-4;

export function midpoint(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
}

function sub(a, b) {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function len(v) {
  return Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
}

/** Heading about +Y, Three.js convention: rotating (0,0,1) by t gives (sin t, 0, cos t). */
function heading(v) {
  return Math.atan2(v.x, v.z);
}

function wrapAngle(a) {
  while (a <= -Math.PI) a += 2 * Math.PI;
  while (a > Math.PI) a -= 2 * Math.PI;
  return a;
}

export function twoHandTransform(prevL, prevR, curL, curR) {
  const d0 = sub(prevR, prevL);
  const d1 = sub(curR, curL);

  const l0 = len(d0);
  const scale = l0 < EPS ? 1 : len(d1) / l0;

  const h0 = Math.hypot(d0.x, d0.z);
  const h1 = Math.hypot(d1.x, d1.z);
  const yaw = h0 < EPS || h1 < EPS ? 0 : wrapAngle(heading(d1) - heading(d0));

  const translate = sub(midpoint(curL, curR), midpoint(prevL, prevR));
  return { scale, yaw, translate };
}

/**
 * New position of a point (the model origin) that is held by both hands:
 * scale and rotate it about the previous midpoint, then carry it along.
 */
export function applyTwoHandToPosition(pos, prevMid, t) {
  const rx = (pos.x - prevMid.x) * t.scale;
  const ry = (pos.y - prevMid.y) * t.scale;
  const rz = (pos.z - prevMid.z) * t.scale;
  const c = Math.cos(t.yaw);
  const s = Math.sin(t.yaw);
  return {
    x: prevMid.x + t.translate.x + rx * c + rz * s,
    y: prevMid.y + t.translate.y + ry,
    z: prevMid.z + t.translate.z - rx * s + rz * c,
  };
}

export function isTwoHandActive({ leftPinching, rightPinching, leftNear, rightNear, tapeMode }) {
  return Boolean(leftPinching && rightPinching && leftNear && rightNear && !tapeMode);
}

/** New total display zoom after multiplying by `factor`, kept in range. */
export function clampNavScale(current, factor) {
  return Math.min(Math.max(current * factor, NAV_SCALE_MIN), NAV_SCALE_MAX);
}

/**
 * How much a two-hand stretch has grown/shrunk the model since it engaged:
 * the ratio of the current display zoom to the zoom when the gesture began.
 * Used both for the live dimensions-label preview and as the resize POST
 * factor on release.
 */
export function stretchFactor(navScaleAtEngage, navScaleNow) {
  if (!(navScaleAtEngage > 0)) return 1;
  return navScaleNow / navScaleAtEngage;
}
