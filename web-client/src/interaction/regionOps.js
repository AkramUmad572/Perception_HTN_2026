/**
 * Region ops: vertex-space edits applied within a smooth falloff around a
 * Selection (interaction/selection.js). Pure: works on plain vertex arrays
 * ({x,y,z} or {r,g,b}), not BufferGeometry, so Node can test the math.
 * main.js reads/writes the loaded model's geometry attributes with these.
 *
 * A small local voice/text table (parseRegionCommand) maps simple utterances
 * ("bigger", "pull out", "paint red", ...) to one of these ops, so a hand
 * edit with an active selection can run client-side with no server call.
 */

function sub(a, b) {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}
function add(a, b) {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}
function scale(v, s) {
  return { x: v.x * s, y: v.y * s, z: v.z * s };
}
function length(v) {
  return Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
}
function dot(a, b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

/**
 * 1 at the selection centre, 0 at `radius * falloffMult` and beyond, smoothly
 * blended in between. A point selection (radius 0) affects only its own spot.
 */
export function falloff(distance, radius, falloffMult = 1.5) {
  if (!(radius > 0)) return distance <= 1e-9 ? 1 : 0;
  const edge = radius * falloffMult;
  if (distance >= edge) return 0;
  if (distance <= radius) return 1;
  const t = (edge - distance) / (edge - radius);
  return t * t * (3 - 2 * t); // smoothstep
}

/** Scale vertices toward/away from the selection centre; factor > 1 grows. */
export function scaleRegion(vertices, selection, factor, falloffMult = 1.5) {
  return vertices.map((v) => {
    const offset = sub(v, selection.center);
    const w = falloff(length(offset), selection.radius, falloffMult);
    if (w <= 0) return v;
    return add(selection.center, scale(offset, 1 + (factor - 1) * w));
  });
}

/** Push vertices along the selection normal by up to `amountM` metres (+out / -in). */
export function pullRegion(vertices, selection, amountM, falloffMult = 1.5) {
  return vertices.map((v) => {
    const w = falloff(length(sub(v, selection.center)), selection.radius, falloffMult);
    if (w <= 0) return v;
    return add(v, scale(selection.normal, amountM * w));
  });
}

/** Flatten vertices onto the plane through the selection centre, normal `selection.normal`. */
export function flattenRegion(vertices, selection, falloffMult = 1.5) {
  return vertices.map((v) => {
    const offset = sub(v, selection.center);
    const w = falloff(length(offset), selection.radius, falloffMult);
    if (w <= 0) return v;
    const h = dot(offset, selection.normal);
    return sub(v, scale(selection.normal, h * w));
  });
}

/**
 * Laplacian-style smoothing: blend each affected vertex toward the average
 * of its neighbours. `neighbors[i]` is the list of vertex indices adjacent
 * to vertex `i` (built once from the geometry's index buffer).
 */
export function smoothRegion(vertices, neighbors, selection, strength = 0.5, falloffMult = 1.5) {
  return vertices.map((v, i) => {
    const w = falloff(length(sub(v, selection.center)), selection.radius, falloffMult) * strength;
    const nbrs = neighbors[i];
    if (w <= 0 || !nbrs || !nbrs.length) return v;
    const avg = scale(
      nbrs.reduce((a, j) => add(a, vertices[j]), { x: 0, y: 0, z: 0 }),
      1 / nbrs.length
    );
    return add(v, scale(sub(avg, v), w));
  });
}

/** Blend `rgb` (0-1 floats) into each vertex colour within the falloff. */
export function paintRegion(colors, positions, selection, rgb, falloffMult = 1.5) {
  return colors.map((c, i) => {
    const w = falloff(length(sub(positions[i], selection.center)), selection.radius, falloffMult);
    if (w <= 0) return c;
    return {
      r: c.r + (rgb.r - c.r) * w,
      g: c.g + (rgb.g - c.g) * w,
      b: c.b + (rgb.b - c.b) * w,
    };
  });
}

/** Small colour-name table for "paint <colour>" — mirrors backend/ai/intent.py's COLOR_MAP. */
export const REGION_COLOR_MAP = {
  yellow: "#FFD700", gold: "#FFD700",
  red: "#E53935",
  blue: "#1E88E5", navy: "#0D47A1",
  green: "#43A047", mint: "#66BB6A",
  silver: "#C0C0C0", grey: "#C0C0C0", gray: "#C0C0C0",
  black: "#212121",
  white: "#FAFAFA", cream: "#FFF3E0",
  orange: "#FB8C00",
  purple: "#8E24AA",
  pink: "#EC407A",
  brown: "#795548", bronze: "#CD7F32",
};

/** How far a single "bigger"/"pull out"/etc. step moves the region. */
export const REGION_SCALE_STEP = 1.2;
export const REGION_PULL_STEP_M = 0.01;

/**
 * Parse a simple region-edit utterance into { op, ...params }, or null if it
 * isn't one. `op` is one of "scale" | "pull" | "flatten" | "smooth" | "paint".
 */
export function parseRegionCommand(text) {
  const t = (text || "").toLowerCase().trim();
  if (!t) return null;
  if (/\b(bigger|larger|grow|puff(?:\s+it)?\s+out)\b/.test(t)) {
    return { op: "scale", factor: REGION_SCALE_STEP };
  }
  if (/\b(smaller|shrink)\b/.test(t)) {
    return { op: "scale", factor: 1 / REGION_SCALE_STEP };
  }
  if (/\bpull\b.*\bout\b|\bpull\s+it\s+out\b/.test(t)) {
    return { op: "pull", amountM: REGION_PULL_STEP_M };
  }
  if (/\bpush\b.*\bin\b|\bpush\s+it\s+in\b/.test(t)) {
    return { op: "pull", amountM: -REGION_PULL_STEP_M };
  }
  if (/\bflatten\b/.test(t)) return { op: "flatten" };
  if (/\bsmooth\b/.test(t)) return { op: "smooth" };
  const paint = t.match(/\bpaint\s+(?:it\s+)?(\w+)\b/);
  if (paint) {
    const hex = REGION_COLOR_MAP[paint[1]];
    if (hex) return { op: "paint", color: hex };
  }
  return null;
}
