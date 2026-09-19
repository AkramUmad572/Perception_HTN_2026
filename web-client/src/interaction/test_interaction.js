/**
 * Tests for the pure interaction math (no Three.js).
 *
 * Run with: node web-client/src/interaction/test_interaction.js
 */

import {
  distanceM,
  formatLength,
  snap,
  FINE_MODE_FACTOR,
  applyFineMode,
  formatDimensions,
  realSize,
  labelPosition,
  tapeNextStep,
  realScaleFor,
} from "./measure.js";
import {
  twoHandTransform,
  midpoint,
  applyTwoHandToPosition,
  isTwoHandActive,
  clampNavScale,
  NAV_SCALE_MIN,
  NAV_SCALE_MAX,
} from "./twoHand.js";
import {
  averageVec,
  normalizeVec,
  strokeLength,
  dominantPart,
  selectionFromStroke,
  POINT_STROKE_LEN_M,
  LASSO_SNAP_SHARE,
} from "./selection.js";
import {
  falloff,
  scaleRegion,
  pullRegion,
  flattenRegion,
  smoothRegion,
  paintRegion,
  parseRegionCommand,
  REGION_COLOR_MAP,
} from "./regionOps.js";

const assert = (condition, message) => {
  if (!condition) throw new Error(`ASSERTION FAILED: ${message}`);
};
const near = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;
const nearVec = (v, w, eps = 1e-6) => near(v.x, w.x, eps) && near(v.y, w.y, eps) && near(v.z, w.z, eps);
const eq = (got, want) => assert(got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

const results = { passed: 0, failed: 0 };
function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    results.passed++;
  } catch (e) {
    console.log(`  ✗ ${name}`);
    console.log(`      ${e.message}`);
    results.failed++;
  }
}

const v = (x, y, z) => ({ x, y, z });

console.log("=".repeat(60));
console.log("INTERACTION MATH TESTS");
console.log("=".repeat(60));

console.log("\n=== measure.js ===");

test("distanceM is Euclidean metres", () => {
  assert(near(distanceM(v(0, 0, 0), v(0.03, 0.04, 0)), 0.05), "3-4-5");
});

test("formatLength picks mm / cm / m", () => {
  eq(formatLength(0.0042), "4.2 mm");
  eq(formatLength(0.125), "12.5 cm");
  eq(formatLength(1.2), "1.20 m");
  eq(formatLength(0), "0.0 mm");
});

test("formatLength rounds across unit boundaries", () => {
  eq(formatLength(0.00999), "1.0 cm");
  eq(formatLength(0.99999), "1.00 m");
});

test("formatLength handles non-finite input", () => {
  eq(formatLength(NaN), "--");
  eq(formatLength(Infinity), "--");
});

test("snap rounds to the step", () => {
  assert(near(snap(0.0123, 0.001), 0.012), "1 mm step");
  eq(snap(7, 5), 5);
  eq(snap(8, 5), 10);
  eq(snap(3, 0), 3);
});

test("fine mode scales movement by 0.1", () => {
  eq(FINE_MODE_FACTOR, 0.1);
  assert(near(applyFineMode(0.05, true), 0.005), "fine");
  eq(applyFineMode(0.05, false), 0.05);
});

test("formatDimensions is W × H × D", () => {
  eq(formatDimensions(v(0.125, 0.0042, 0.03)), "12.5 cm × 4.2 mm × 3.0 cm");
});

test("realSize multiplies raw bounds by the object scale", () => {
  assert(nearVec(realSize(v(1, 2, 4), 0.05), v(0.05, 0.1, 0.2)), "scaled");
});

test("labelPosition sits beside the box along the right vector", () => {
  const p = labelPosition(v(-0.1, 0, -0.1), v(0.1, 0.2, 0.1), v(1, 0, 0));
  assert(nearVec(p, v(0.13, 0.1, 0)), JSON.stringify(p));
});

test("tapeNextStep alternates start / end", () => {
  eq(tapeNextStep({ hasStart: false, hasEnd: false }), "start");
  eq(tapeNextStep({ hasStart: true, hasEnd: false }), "end");
  eq(tapeNextStep({ hasStart: true, hasEnd: true }), "start");
});

test("realScaleFor: CAD GLBs are already metres, sculpts use their display scale", () => {
  eq(realScaleFor("cad", 0.5), 1);
  eq(realScaleFor("mesh", 0.5), 0.5);
  eq(realScaleFor(undefined, 0.5), 0.5);
});

console.log("\n=== twoHand.js ===");

test("still hands produce the identity transform", () => {
  const t = twoHandTransform(v(-0.1, 0, 0), v(0.1, 0, 0), v(-0.1, 0, 0), v(0.1, 0, 0));
  assert(near(t.scale, 1) && near(t.yaw, 0) && nearVec(t.translate, v(0, 0, 0)), JSON.stringify(t));
});

test("spreading the hands scales by the span ratio", () => {
  const t = twoHandTransform(v(-0.1, 0, 0), v(0.1, 0, 0), v(-0.2, 0, 0), v(0.2, 0, 0));
  assert(near(t.scale, 2), `scale ${t.scale}`);
  assert(near(t.yaw, 0), `yaw ${t.yaw}`);
});

test("moving both hands translates by the midpoint delta", () => {
  const t = twoHandTransform(v(-0.1, 0, 0), v(0.1, 0, 0), v(0, 0, 0), v(0.2, 0, 0));
  assert(nearVec(t.translate, v(0.1, 0, 0)), JSON.stringify(t.translate));
  assert(near(t.scale, 1), `scale ${t.scale}`);
});

test("twisting about vertical gives yaw (Three.js Y convention)", () => {
  // Right hand swings from +x to -z: heading pi/2 -> pi, so yaw = +pi/2.
  const t = twoHandTransform(v(-0.1, 0, 0), v(0.1, 0, 0), v(0, 0, 0.1), v(0, 0, -0.1));
  assert(near(t.yaw, Math.PI / 2), `yaw ${t.yaw}`);
  assert(near(t.scale, 1), `scale ${t.scale}`);
});

test("yaw wraps into (-pi, pi]", () => {
  // Heading from just under +pi to just over -pi is a tiny turn, not ~2pi.
  const t = twoHandTransform(v(0, 0, 0), v(0.01, 0, -1), v(0, 0, 0), v(-0.01, 0, -1));
  assert(Math.abs(t.yaw) < 0.1, `yaw ${t.yaw}`);
});

test("degenerate previous span keeps scale 1", () => {
  const t = twoHandTransform(v(0, 0, 0), v(0, 0, 0), v(-0.1, 0, 0), v(0.1, 0, 0));
  assert(near(t.scale, 1) && near(t.yaw, 0), JSON.stringify(t));
});

test("a vertical-only span gives no yaw", () => {
  const t = twoHandTransform(v(0, 0, 0), v(0, 0.2, 0), v(0, 0, 0), v(0, 0.2, 0.00001));
  assert(near(t.yaw, 0), `yaw ${t.yaw}`);
});

test("midpoint averages the hands", () => {
  assert(nearVec(midpoint(v(0, 0, 0), v(0.2, 0.4, -0.2)), v(0.1, 0.2, -0.1)), "mid");
});

test("applyTwoHandToPosition scales about the hands then translates", () => {
  const p = applyTwoHandToPosition(v(0.1, 0, 0), v(0, 0, 0), { scale: 2, yaw: 0, translate: v(0, 0.1, 0) });
  assert(nearVec(p, v(0.2, 0.1, 0)), JSON.stringify(p));
});

test("applyTwoHandToPosition rotates about world Y", () => {
  const p = applyTwoHandToPosition(v(0, 0, 0.1), v(0, 0, 0), { scale: 1, yaw: Math.PI / 2, translate: v(0, 0, 0) });
  assert(nearVec(p, v(0.1, 0, 0)), JSON.stringify(p));
});

test("two-hand mode needs both hands pinching near the model, tape off", () => {
  const on = { leftPinching: true, rightPinching: true, leftNear: true, rightNear: true, tapeMode: false };
  eq(isTwoHandActive(on), true);
  eq(isTwoHandActive({ ...on, leftPinching: false }), false);
  eq(isTwoHandActive({ ...on, rightPinching: false }), false);
  eq(isTwoHandActive({ ...on, leftNear: false }), false);
  eq(isTwoHandActive({ ...on, rightNear: false }), false);
  eq(isTwoHandActive({ ...on, tapeMode: true }), false);
});

test("clampNavScale keeps the zoom in range", () => {
  eq(NAV_SCALE_MIN, 0.1);
  eq(NAV_SCALE_MAX, 10);
  eq(clampNavScale(9, 2), 10);
  eq(clampNavScale(0.2, 0.1), 0.1);
  assert(near(clampNavScale(1, 1.5), 1.5), "in range");
});

console.log("\n=== selection.js ===");

test("averageVec is the centroid", () => {
  assert(nearVec(averageVec([v(0, 0, 0), v(2, 0, 0), v(1, 3, 0)]), v(1, 1, 0)), "centroid");
  assert(nearVec(averageVec([]), v(0, 0, 0)), "empty");
});

test("normalizeVec unit-lengths a vector, defaults degenerate input to +Y", () => {
  const n = normalizeVec(v(0, 0, 5));
  assert(near(n.z, 1) && near(n.x, 0), "unit z");
  assert(nearVec(normalizeVec(v(0, 0, 0)), v(0, 1, 0)), "degenerate → up");
});

test("strokeLength sums the polyline", () => {
  assert(near(strokeLength([v(0, 0, 0), v(1, 0, 0), v(1, 1, 0)]), 2), "L-shape");
  eq(strokeLength([v(0, 0, 0)]), 0);
});

test("dominantPart snaps only above the share threshold", () => {
  const hits = (parts) => parts.map((part) => ({ point: v(0, 0, 0), normal: v(0, 1, 0), part }));
  eq(dominantPart(hits(["ear_l", "ear_l", "ear_l", "body"])), "ear_l");
  eq(dominantPart(hits(["ear_l", "body", "ear_r"])), null);
  eq(dominantPart(hits([null, null, "ear_l"])), null);
  eq(dominantPart([]), null);
  eq(LASSO_SNAP_SHARE, 0.6);
});

test("selectionFromStroke: a short single hit is a point (radius 0)", () => {
  const hit = { point: v(0.1, 0.2, 0.3), normal: v(1, 0, 0), part: null };
  const sel = selectionFromStroke([hit]);
  assert(nearVec(sel.center, v(0.1, 0.2, 0.3)), "center");
  assert(nearVec(sel.normal, v(1, 0, 0)), "normal");
  eq(sel.radius, 0);
  assert(Array.isArray(sel.parts) && sel.parts.length === 0, "no part");
});

test("selectionFromStroke: a drag becomes a lasso with a positive radius", () => {
  const hits = [
    { point: v(-0.05, 0, 0), normal: v(0, 1, 0), part: "body" },
    { point: v(0.05, 0, 0), normal: v(0, 1, 0), part: "body" },
  ];
  const sel = selectionFromStroke(hits);
  assert(sel.radius > 0, `radius ${sel.radius}`);
  assert(strokeLength(hits.map((h) => h.point)) >= POINT_STROKE_LEN_M, "long enough to be a lasso");
  eq(sel.parts[0], "body");
});

test("selectionFromStroke returns null for no hits", () => {
  eq(selectionFromStroke([]), null);
  eq(selectionFromStroke(null), null);
});

console.log("\n=== regionOps.js ===");

test("falloff is 1 at the centre and 0 past radius*falloffMult", () => {
  eq(falloff(0, 0.02), 1);
  eq(falloff(0.05, 0.02, 1.5), 0);
  const mid = falloff(0.025, 0.02, 1.5);
  assert(mid > 0 && mid < 1, `mid ${mid}`);
});

test("falloff on a point selection (radius 0) only hits its own spot", () => {
  eq(falloff(0, 0), 1);
  eq(falloff(0.001, 0), 0);
});

test("scaleRegion grows vertices near the centre, leaves far ones alone", () => {
  const sel = { center: v(0, 0, 0), normal: v(0, 1, 0), radius: 0.05 };
  const near_ = scaleRegion([v(0.01, 0, 0)], sel, 2)[0];
  assert(near_.x > 0.01, `grew: ${near_.x}`);
  const far = scaleRegion([v(10, 0, 0)], sel, 2)[0];
  assert(nearVec(far, v(10, 0, 0)), "untouched far away");
});

test("pullRegion moves vertices along the normal, scaled by amount and falloff", () => {
  const sel = { center: v(0, 0, 0), normal: v(0, 0, 1), radius: 0.05 };
  const pulled = pullRegion([v(0, 0, 0)], sel, 0.01)[0];
  assert(nearVec(pulled, v(0, 0, 0.01)), JSON.stringify(pulled));
  const pushed = pullRegion([v(0, 0, 0)], sel, -0.01)[0];
  assert(nearVec(pushed, v(0, 0, -0.01)), JSON.stringify(pushed));
});

test("flattenRegion zeroes the component along the normal near the centre", () => {
  const sel = { center: v(0, 0, 0), normal: v(0, 1, 0), radius: 0.05 };
  const flat = flattenRegion([v(0, 0.02, 0)], sel)[0];
  assert(near(flat.y, 0, 1e-6), `y ${flat.y}`);
});

test("smoothRegion blends toward the neighbour average", () => {
  const verts = [v(0, 1, 0), v(0, 0, 0), v(0, 0, 0)];
  const neighbors = [[1, 2], [0], [0]];
  const sel = { center: v(0, 1, 0), normal: v(0, 1, 0), radius: 0.5 };
  const out = smoothRegion(verts, neighbors, sel, 1.0)[0];
  assert(nearVec(out, v(0, 0, 0)), JSON.stringify(out));
});

test("smoothRegion leaves vertices with no neighbours untouched", () => {
  const verts = [v(1, 1, 1)];
  const sel = { center: v(1, 1, 1), normal: v(0, 1, 0), radius: 1 };
  const out = smoothRegion(verts, [[]], sel, 1.0)[0];
  assert(nearVec(out, v(1, 1, 1)), "no neighbours");
});

test("paintRegion blends colour by falloff", () => {
  const sel = { center: v(0, 0, 0), normal: v(0, 1, 0), radius: 0.05 };
  const painted = paintRegion([{ r: 0, g: 0, b: 0 }], [v(0, 0, 0)], sel, { r: 1, g: 0, b: 0 })[0];
  assert(near(painted.r, 1) && near(painted.g, 0), JSON.stringify(painted));
  const untouched = paintRegion([{ r: 0, g: 0, b: 0 }], [v(10, 0, 0)], sel, { r: 1, g: 0, b: 0 })[0];
  assert(near(untouched.r, 0), "far away unpainted");
});

test("parseRegionCommand maps the local voice table", () => {
  eq(parseRegionCommand("bigger").op, "scale");
  assert(parseRegionCommand("bigger").factor > 1, "grows");
  eq(parseRegionCommand("a bit smaller").op, "scale");
  assert(parseRegionCommand("smaller").factor < 1, "shrinks");
  eq(parseRegionCommand("pull it out").op, "pull");
  assert(parseRegionCommand("pull it out").amountM > 0, "outward");
  eq(parseRegionCommand("push in").op, "pull");
  assert(parseRegionCommand("push in").amountM < 0, "inward");
  eq(parseRegionCommand("flatten that").op, "flatten");
  eq(parseRegionCommand("smooth it").op, "smooth");
  const paint = parseRegionCommand("paint it red");
  eq(paint.op, "paint");
  eq(paint.color, REGION_COLOR_MAP.red);
});

test("parseRegionCommand returns null for anything else", () => {
  eq(parseRegionCommand("build me a box"), null);
  eq(parseRegionCommand("paint it a color that doesn't exist"), null);
  eq(parseRegionCommand(""), null);
  eq(parseRegionCommand(undefined), null);
});

console.log("\n" + "=".repeat(60));
console.log(`TOTAL: ${results.passed}/${results.passed + results.failed} passed`);
if (results.failed > 0) process.exit(1);
