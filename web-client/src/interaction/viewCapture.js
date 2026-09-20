/**
 * Pure 2D helpers for the semantic-edit capture.
 *
 * main.js does the Three.js part (render the current view to a canvas, and
 * `point.project(camera)` to get normalised device coordinates). Everything
 * after that is plain arithmetic and lives here so Node can test it without a
 * GPU or a WebXR session.
 *
 * The circle these place is what tells the image model where to make the
 * change — see docs/superpowers/specs/2026-09-19-semantic-mesh-edits-design.md.
 */

/** NDC ({x,y} each -1..1, Y up) -> canvas pixels (Y down). */
export function ndcToPixels(ndc, width, height) {
  return {
    x: ((ndc.x + 1) / 2) * width,
    y: ((1 - ndc.y) / 2) * height,
  };
}

/**
 * The circle is a fixed share of the view rather than a projected 3D radius:
 * the tap is a point (radius 0), and "close enough" beats a size the user
 * cannot control anyway.
 */
export function circleRadiusPx(width, height, fraction = 0.22) {
  return Math.min(width, height) * fraction;
}

/**
 * Keep the circle fully on-canvas so the edit region is never clipped.
 *
 * `.project(camera)` returns coordinates outside -1..1 for anything off-screen
 * or behind the camera, so cx/cy can arrive wildly out of range or non-finite.
 * Everything is clamped rather than trusted.
 */
export function clampCircle(cx, cy, r, width, height) {
  const radius = Math.min(
    Number.isFinite(r) ? Math.abs(r) : circleRadiusPx(width, height),
    width / 2,
    height / 2
  );
  const safe = (v, span) =>
    Number.isFinite(v) ? Math.min(Math.max(v, radius), span - radius) : span / 2;
  return { cx: safe(cx, width), cy: safe(cy, height), r: radius };
}
