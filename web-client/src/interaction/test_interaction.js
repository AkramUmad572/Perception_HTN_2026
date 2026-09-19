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

console.log("\n" + "=".repeat(60));
console.log(`TOTAL: ${results.passed}/${results.passed + results.failed} passed`);
if (results.failed > 0) process.exit(1);
