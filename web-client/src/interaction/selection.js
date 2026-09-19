/**
 * Building a Selection from a right-hand pinch/lasso stroke on the model.
 * Pure: works with plain {x,y,z} points and raycast hit records collected by
 * main.js; no Three.js import, so Node can test the math.
 *
 * A "hit" is one raycast sample along the stroke:
 *   { point: {x,y,z}, normal: {x,y,z}, part: string | null }
 * `part` is the GLB node name the ray struck (null when unknown, e.g. a
 * single mesh with no named sub-parts).
 *
 * The result matches app/models.py's Selection: { parts, center, normal, radius }.
 */

/** A stroke shorter than this (metres) is a point, not a lasso. */
export const POINT_STROKE_LEN_M = 0.01;
/** Snap to a whole part when at least this share of hits landed on it. */
export const LASSO_SNAP_SHARE = 0.6;

function sub(a, b) {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function distanceOf(a, b) {
  const d = sub(b, a);
  return Math.sqrt(d.x * d.x + d.y * d.y + d.z * d.z);
}

export function averageVec(vecs) {
  if (!vecs || !vecs.length) return { x: 0, y: 0, z: 0 };
  const sum = vecs.reduce(
    (a, v) => ({ x: a.x + v.x, y: a.y + v.y, z: a.z + v.z }),
    { x: 0, y: 0, z: 0 }
  );
  const n = vecs.length;
  return { x: sum.x / n, y: sum.y / n, z: sum.z / n };
}

export function normalizeVec(v) {
  const len = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  if (!(len > 1e-9)) return { x: 0, y: 1, z: 0 };
  return { x: v.x / len, y: v.y / len, z: v.z / len };
}

/** Total length of the polyline through `points`, in the units they're given in. */
export function strokeLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    total += distanceOf(points[i - 1], points[i]);
  }
  return total;
}

/**
 * The part name the stroke should snap onto, or null if no single part
 * covers at least `minShare` of the hits (or none named a part at all).
 */
export function dominantPart(hits, minShare = LASSO_SNAP_SHARE) {
  if (!hits || !hits.length) return null;
  const counts = new Map();
  for (const h of hits) {
    if (!h.part) continue;
    counts.set(h.part, (counts.get(h.part) || 0) + 1);
  }
  let best = null;
  let bestCount = 0;
  for (const [part, count] of counts) {
    if (count > bestCount) {
      best = part;
      bestCount = count;
    }
  }
  return best && bestCount / hits.length >= minShare ? best : null;
}

/**
 * Build a Selection from the raycast hits of one pinch stroke.
 * Returns null for an empty stroke.
 */
export function selectionFromStroke(hits) {
  if (!hits || !hits.length) return null;
  const points = hits.map((h) => h.point);
  const center = averageVec(points);
  const normal = normalizeVec(averageVec(hits.map((h) => h.normal)));
  const isPoint = hits.length < 2 || strokeLength(points) < POINT_STROKE_LEN_M;
  const radius = isPoint ? 0 : points.reduce((m, p) => Math.max(m, distanceOf(p, center)), 0);
  const snapped = dominantPart(hits);
  return { parts: snapped ? [snapped] : [], center, normal, radius };
}
