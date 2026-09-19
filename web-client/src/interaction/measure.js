/**
 * Pure measurement helpers. No Three.js import, so Node can test them.
 * Vectors are plain {x, y, z} objects in metres.
 */

/** Hand movement multiplier while fine mode (off-hand pinch) is held. */
export const FINE_MODE_FACTOR = 0.1;

export function distanceM(a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dz = b.z - a.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/** "4.2 mm" under 1 cm, "12.5 cm" under 1 m, otherwise "1.20 m". */
export function formatLength(m) {
  if (!Number.isFinite(m)) return "--";
  const a = Math.abs(m);
  // Round first, then pick the unit, so 9.99 mm reads "1.0 cm", not "10.0 mm".
  const mm = Math.round(a * 10000) / 10;
  if (mm < 10) return `${mm.toFixed(1)} mm`;
  const cm = Math.round(a * 1000) / 10;
  if (cm < 100) return `${cm.toFixed(1)} cm`;
  return `${a.toFixed(2)} m`;
}

export function snap(value, step) {
  if (!(step > 0)) return value;
  return Math.round(value / step) * step;
}

export function applyFineMode(delta, fine) {
  return fine ? delta * FINE_MODE_FACTOR : delta;
}

/** W × H × D (GLB is Y-up: x = width, y = height, z = depth). */
export function formatDimensions(size) {
  return [size.x, size.y, size.z].map(formatLength).join(" × ");
}

/** Raw GLB bounds times the object's own (real-size) scale. */
export function realSize(raw, scale) {
  return { x: raw.x * scale, y: raw.y * scale, z: raw.z * scale };
}

/**
 * Where a label floats: box centre pushed out along `right` (unit vector)
 * by the box's half-extent in that direction plus a margin.
 */
export function labelPosition(min, max, right, margin = 0.03) {
  const c = { x: (min.x + max.x) / 2, y: (min.y + max.y) / 2, z: (min.z + max.z) / 2 };
  const half =
    (Math.abs(right.x) * (max.x - min.x) +
      Math.abs(right.y) * (max.y - min.y) +
      Math.abs(right.z) * (max.z - min.z)) / 2;
  const d = half + margin;
  return { x: c.x + right.x * d, y: c.y + right.y * d, z: c.z + right.z * d };
}

/** Tape measure: which endpoint the next pinch places. */
export function tapeNextStep(state) {
  return state.hasStart && !state.hasEnd ? "end" : "start";
}
