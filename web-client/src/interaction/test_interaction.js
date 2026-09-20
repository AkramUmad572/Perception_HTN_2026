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
  stretchFactor,
  NAV_SCALE_MIN,
  NAV_SCALE_MAX,
} from "./twoHand.js";
import {
  paramsForPart,
  spokenParamName,
  paramRows,
  rowOffsetY,
  pickRow,
  dragParamValue,
  paramLabel,
  paramPanelLines,
  PARAM_ROW_SPACING,
} from "./paramPanel.js";
import {
  averageVec,
  normalizeVec,
  strokeLength,
  dominantPart,
  selectionFromStroke,
  isTapRelease,
  POINT_STROKE_LEN_M,
  LASSO_SNAP_SHARE,
  TAP_MAX_MS,
  TAP_MAX_MOVE_M,
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
import {
  parseDimensionCommand,
  resolveParamKey,
  planPartEdit,
  PART_SCALE_STEP,
} from "./partEdit.js";

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

test("stretchFactor is the ratio of zoom now to zoom at gesture start", () => {
  eq(stretchFactor(1, 1), 1);
  assert(near(stretchFactor(1, 2), 2), "doubled");
  assert(near(stretchFactor(2, 1), 0.5), "halved");
  assert(near(stretchFactor(4, 6), 1.5), "1.5x from a non-1 start");
  eq(stretchFactor(0, 5), 1);
  eq(stretchFactor(-1, 5), 1);
});

console.log("\n=== paramPanel.js ===");

test("paramsForPart matches a plain prefix", () => {
  const params = { ear_length_mm: 16, ear_width_mm: 8, head_radius_mm: 20 };
  eq(JSON.stringify(paramsForPart(params, "ear")), JSON.stringify({ ear_length_mm: 16, ear_width_mm: 8 }));
});

test("paramsForPart strips a mirrored/repeated side suffix", () => {
  const params = { ear_length_mm: 16, ear_width_mm: 8, head_radius_mm: 20 };
  eq(JSON.stringify(paramsForPart(params, "ear_l")), JSON.stringify({ ear_length_mm: 16, ear_width_mm: 8 }));
  eq(JSON.stringify(paramsForPart(params, "ear_r")), JSON.stringify({ ear_length_mm: 16, ear_width_mm: 8 }));

  const wheel = { wheel_diameter_mm: 40 };
  eq(JSON.stringify(paramsForPart(wheel, "wheel_fl")), JSON.stringify({ wheel_diameter_mm: 40 }));
  eq(JSON.stringify(paramsForPart(wheel, "wheel_2")), JSON.stringify({ wheel_diameter_mm: 40 }));
  // Repeated stripping: "ear_l_2" still reaches "ear_*".
  eq(JSON.stringify(paramsForPart({ ear_length_mm: 16 }, "ear_l_2")), JSON.stringify({ ear_length_mm: 16 }));
});

test("paramsForPart is empty for no part or no match", () => {
  eq(JSON.stringify(paramsForPart({ a_mm: 1 }, "")), "{}");
  eq(JSON.stringify(paramsForPart({ a_mm: 1 }, "b")), "{}");
  eq(JSON.stringify(paramsForPart({}, "a")), "{}");
});

test("spokenParamName drops the trailing unit", () => {
  eq(spokenParamName("ear_length_mm"), "ear length");
  eq(spokenParamName("head_radius_deg"), "head radius");
  eq(spokenParamName("width_mm"), "width");
  eq(spokenParamName("mm"), "mm");
  eq(spokenParamName(""), "that");
});

test("paramRows is sorted for a stable layout", () => {
  const rows = paramRows({ b_mm: 2, a_mm: 1 });
  eq(JSON.stringify(rows), JSON.stringify([{ name: "a_mm", value: 1 }, { name: "b_mm", value: 2 }]));
});

test("rowOffsetY centres the rows, top first", () => {
  eq(PARAM_ROW_SPACING, 0.035);
  assert(near(rowOffsetY(0, 1), 0), "single row centred");
  assert(near(rowOffsetY(0, 2), PARAM_ROW_SPACING / 2), "row 0 is on top");
  assert(near(rowOffsetY(1, 2), -PARAM_ROW_SPACING / 2), "row 1 is below");
});

test("pickRow finds the nearest row", () => {
  const rows = paramRows({ a_mm: 1, b_mm: 2, c_mm: 3 });
  eq(pickRow(rows, rowOffsetY(0, 3)), 0);
  eq(pickRow(rows, rowOffsetY(2, 3)), 2);
  eq(pickRow(rows, 0), 1);
  eq(pickRow([], 0), -1);
});

test("dragParamValue moves by hand delta, snapped to 1 mm", () => {
  eq(dragParamValue(10, 0.005, false), 15);
  eq(dragParamValue(10, -0.003, false), 7);
});

test("dragParamValue halves the delta in fine mode", () => {
  eq(dragParamValue(10, 0.01, true), 11);
});

test("dragParamValue never drops to zero or below", () => {
  eq(dragParamValue(2, -0.05, false), 1);
});

test("paramLabel formats name and value together", () => {
  eq(paramLabel("ear_length_mm", 16), "ear length: 1.6 cm");
  eq(paramLabel("head_radius_mm", 4), "head radius: 4.0 mm");
});

test("paramPanelLines marks the active row", () => {
  const rows = paramRows({ a_mm: 1, b_mm: 2 });
  const lines = paramPanelLines(rows, 1);
  eq(lines[0], "a: 1.0 mm");
  eq(lines[1], "> b: 2.0 mm");
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

test("isTapRelease: quick and still is a tap, slow or far is a hold", () => {
  eq(TAP_MAX_MS, 250);
  eq(TAP_MAX_MOVE_M, 0.02);
  eq(isTapRelease(100, 0.005), true);
  eq(isTapRelease(249, 0.019), true);
  eq(isTapRelease(250, 0.005), false, "at the duration limit is a hold");
  eq(isTapRelease(300, 0.005), false, "too slow is a hold");
  eq(isTapRelease(100, 0.02), false, "at the movement limit is a hold");
  eq(isTapRelease(100, 0.05), false, "too far is a hold");
  eq(isTapRelease(50, 0.01, 500, 0.1), true, "custom thresholds");
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

console.log("\n=== partEdit.js ===");

// A realistic PARAMS dict, in the shape cad/params.py produces.
const EAR_PARAMS = {
  ear_length_mm: 16,
  ear_base_r_mm: 5.5,
  ear_top_r_mm: 1.6,
  ear_spacing_mm: 20,
  head_radius_mm: 16,
  plate_thickness_mm: 6,
  plate_width_mm: 26,
};

test("parseDimensionCommand reads relative length words", () => {
  eq(parseDimensionCommand("make it longer").axis, "length");
  eq(parseDimensionCommand("make it longer").dir, 1);
  eq(parseDimensionCommand("make it shorter").dir, -1);
  eq(parseDimensionCommand("taller").axis, "length");
});

test("parseDimensionCommand reads width and thickness", () => {
  eq(parseDimensionCommand("make it wider").axis, "width");
  eq(parseDimensionCommand("make it thicker").axis, "thickness");
  eq(parseDimensionCommand("make it thinner").dir, -1);
});

test("parseDimensionCommand reads an explicit millimetre delta", () => {
  const c = parseDimensionCommand("make it 5 mm longer");
  eq(c.axis, "length");
  eq(c.deltaMm, 5);
  eq(c.dir, 1);
});

test("parseDimensionCommand reads centimetres as millimetres", () => {
  eq(parseDimensionCommand("make it 2 cm longer").deltaMm, 20);
});

test("parseDimensionCommand handles an absolute target", () => {
  const c = parseDimensionCommand("make it 20 mm long");
  eq(c.absoluteMm, 20);
  eq(c.axis, "length");
});

test("parseDimensionCommand ignores non-dimensional speech", () => {
  eq(parseDimensionCommand("add wings"), null);
  eq(parseDimensionCommand("give it a hat"), null);
  eq(parseDimensionCommand("make it red"), null);
  eq(parseDimensionCommand("paint it blue"), null);
  eq(parseDimensionCommand("drill a hole"), null);
  eq(parseDimensionCommand(""), null);
  eq(parseDimensionCommand(undefined), null);
});

test("resolveParamKey finds the part's own dimension first", () => {
  eq(resolveParamKey(EAR_PARAMS, "ear_l", "length"), "ear_length_mm");
  eq(resolveParamKey(EAR_PARAMS, "plate", "thickness"), "plate_thickness_mm");
  eq(resolveParamKey(EAR_PARAMS, "plate", "width"), "plate_width_mm");
});

test("resolveParamKey falls back to a radius for size words", () => {
  eq(resolveParamKey(EAR_PARAMS, "head", "size"), "head_radius_mm");
});

test("resolveParamKey returns null when the part has no such dimension", () => {
  eq(resolveParamKey(EAR_PARAMS, "head", "thickness"), null);
  eq(resolveParamKey(EAR_PARAMS, "nonexistent", "length"), null);
  eq(resolveParamKey({}, "ear_l", "length"), null);
});

test("planPartEdit scales by the step and snaps to 1 mm", () => {
  const plan = planPartEdit(EAR_PARAMS, "ear_l", "make it longer");
  eq(plan.key, "ear_length_mm");
  eq(plan.from, 16);
  eq(plan.to, Math.round(16 * PART_SCALE_STEP));
});

test("planPartEdit shrinks for a negative direction", () => {
  const plan = planPartEdit(EAR_PARAMS, "ear_l", "make it shorter");
  assert(plan.to < plan.from, `expected shrink, got ${plan.to}`);
});

test("planPartEdit applies an explicit delta exactly", () => {
  const plan = planPartEdit(EAR_PARAMS, "ear_l", "make it 5 mm longer");
  eq(plan.to, 21);
});

test("planPartEdit applies an absolute target exactly", () => {
  eq(planPartEdit(EAR_PARAMS, "ear_l", "make it 20 mm long").to, 20);
});

test("planPartEdit never returns a non-positive value", () => {
  const tiny = { ear_length_mm: 2 };
  const plan = planPartEdit(tiny, "ear_l", "make it 50 mm shorter");
  assert(plan === null || plan.to > 0, `got ${JSON.stringify(plan)}`);
});

test("planPartEdit returns null with no part, no match, or no params", () => {
  eq(planPartEdit(EAR_PARAMS, "", "make it longer"), null);
  eq(planPartEdit(EAR_PARAMS, null, "make it longer"), null);
  eq(planPartEdit(EAR_PARAMS, "ear_l", "add wings"), null);
  eq(planPartEdit({}, "ear_l", "make it longer"), null);
  eq(planPartEdit(EAR_PARAMS, "head", "make it thicker"), null);
});

// ---------------------------------------------------------------------------
// Dispatch guard: every utterance that is fast today must STAY on its own path.
// This is the test that would have caught "make it red" being swallowed by the
// semantic-edit rung. A new rung that steals traffic fails here.
// ---------------------------------------------------------------------------
console.log("\n=== dispatch guard ===");

test("sculpt-only region ops are never claimed as a dimension edit", () => {
  // These have no CAD meaning: they mutate vertices. partEdit must leave them
  // to regionOps regardless of lane.
  for (const text of ["smooth", "pull it out", "push it in", "flatten", "paint it red"]) {
    assert(parseRegionCommand(text) !== null, `regionOps should claim ${text}`);
    eq(planPartEdit(EAR_PARAMS, "ear_l", text), null);
  }
});

test("size words are claimed by both, and the LANE decides which wins", () => {
  // "bigger" means vertex-scale a sculpt and PARAMS-scale a CAD part. Both
  // claiming is correct; the caller must pick by session backend, which is
  // why the wiring only consults partEdit on a CAD session.
  for (const text of ["bigger", "smaller"]) {
    assert(parseRegionCommand(text) !== null, `regionOps should claim ${text} for mesh`);
    assert(planPartEdit(EAR_PARAMS, "ear_l", text) !== null,
           `partEdit should claim ${text} for CAD`);
  }
});

test("recolours are never claimed as a dimension edit", () => {
  for (const text of ["make it red", "make it blue", "turn it green",
                      "colour it black", "paint the ears red"]) {
    eq(planPartEdit(EAR_PARAMS, "ear_l", text), null);
  }
});

test("structural additions are never claimed as a dimension edit", () => {
  for (const text of ["add wings", "give it a hat", "put horns on it",
                      "stick a handle on it", "drill a hole", "add a loop"]) {
    eq(planPartEdit(EAR_PARAMS, "ear_l", text), null);
  }
});

test("partEdit claims only what it can actually resolve", () => {
  // resolvable -> claimed
  assert(planPartEdit(EAR_PARAMS, "ear_l", "make it longer") !== null, "should claim");
  // same words, no selected part -> not claimed, falls through to the server
  eq(planPartEdit(EAR_PARAMS, null, "make it longer"), null);
});

console.log("\n" + "=".repeat(60));
console.log(`TOTAL: ${results.passed}/${results.passed + results.failed} passed`);
if (results.failed > 0) process.exit(1);
