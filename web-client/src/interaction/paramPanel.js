/**
 * Pure logic behind the dimension (PARAMS) panel: which named dimensions
 * belong to a pointed-at part, and how a pinch-drag on one row turns hand
 * movement into a new value. No Three.js import, so Node can test it.
 *
 * Values are millimetres, matching the server's PARAMS convention
 * (`<part>_<dimension>_mm`). See backend/cad/params.py:params_for_part /
 * _spoken, which this mirrors so client and server never disagree.
 */
import { snap, applyFineMode, formatLength } from "./measure.js";

// Mirrors backend/cad/params.py:_SIDE_SUFFIX_RE — a mirrored, repeated or
// duplicated part shares its base's dimensions ("ear_l" -> "ear_*",
// "wheel_fl" -> "wheel_*", "wheel_2" -> "wheel_*").
const SIDE_SUFFIX_RE = /_(?:l|r|left|right|fl|fr|rl|rr|front|rear|back|\d+)$/;

/** The PARAMS entries that belong to one GLB part name (mirrors params_for_part). */
export function paramsForPart(params, part) {
  if (!part) return {};
  const prefixes = new Set([`${part}_`]);
  let base = part;
  for (;;) {
    const shorter = base.replace(SIDE_SUFFIX_RE, "");
    if (!shorter || shorter === base) break;
    base = shorter;
    prefixes.add(`${base}_`);
  }
  const out = {};
  for (const [k, v] of Object.entries(params || {})) {
    for (const p of prefixes) {
      if (k.startsWith(p)) {
        out[k] = v;
        break;
      }
    }
  }
  return out;
}

/** "ear_length_mm" -> "ear length": dimension names are read aloud (mirrors _spoken). */
export function spokenParamName(name) {
  const words = String(name).split("_").filter(Boolean);
  const last = words[words.length - 1]?.toLowerCase();
  if (words.length > 1 && ["mm", "cm", "m", "deg"].includes(last)) words.pop();
  return words.join(" ") || "that";
}

/** Sorted {name, value} rows, for a stable panel layout. */
export function paramRows(params) {
  return Object.keys(params || {})
    .sort()
    .map((name) => ({ name, value: params[name] }));
}

/** Vertical spacing between rows in the floating panel, in metres. */
export const PARAM_ROW_SPACING = 0.035;

/** Row `index`'s offset above the panel anchor (row 0 on top). */
export function rowOffsetY(index, count, spacing = PARAM_ROW_SPACING) {
  return ((count - 1) / 2 - index) * spacing;
}

/** Which row a panel-local Y is closest to (for picking up a pinch-drag). */
export function pickRow(rows, localY, spacing = PARAM_ROW_SPACING) {
  if (!rows.length) return -1;
  let best = 0;
  let bestDist = Infinity;
  rows.forEach((_, i) => {
    const d = Math.abs(rowOffsetY(i, rows.length, spacing) - localY);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  });
  return best;
}

/**
 * The row's live value while dragging: vertical hand movement (metres, +up)
 * since the pinch started becomes millimetres, halved while fine mode (the
 * off-hand pinch) is held, then snapped to 1 mm. Never lets a dimension
 * reach zero — the server would reject it anyway.
 */
export function dragParamValue(baseValueMm, deltaM, fine) {
  const deltaMm = applyFineMode(deltaM * 1000, fine);
  return Math.max(snap(baseValueMm + deltaMm, 1), 1);
}

/** The live label text for one row, e.g. "ear length: 1.6 cm". */
export function paramLabel(name, valueMm) {
  return `${spokenParamName(name)}: ${formatLength(valueMm / 1000)}`;
}

/** All panel lines, with the row being dragged marked. */
export function paramPanelLines(rows, activeIndex = -1) {
  return rows.map((r, i) =>
    i === activeIndex ? `> ${paramLabel(r.name, r.value)}` : paramLabel(r.name, r.value)
  );
}
