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
} from "./measure.js";

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

console.log("\n" + "=".repeat(60));
console.log(`TOTAL: ${results.passed}/${results.passed + results.failed} passed`);
if (results.failed > 0) process.exit(1);
