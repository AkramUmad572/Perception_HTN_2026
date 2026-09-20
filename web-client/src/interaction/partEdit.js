/**
 * Dimensional edits on a selected CAD part, resolved client-side.
 *
 * A CAD script carries its sizes in a PARAMS dict (cad/params.py), and the
 * client already holds the current values as `cadParams`. So "make it longer"
 * on a selected part needs no LLM at all: work out which PARAMS key the user
 * means, compute the new value, and POST it to /api/projects/{id}/params.
 * That is a ~67 ms round trip against ~15 s for the same edit through codegen.
 *
 * This mirrors regionOps.parseRegionCommand, which does the same trick for
 * sculpts. Pure functions only, so Node can test them without Three.js.
 *
 * Safety rule: only ever claim an utterance when a real PARAMS key resolves.
 * Anything unclaimed falls through to the server and behaves exactly as it
 * does today, so a miss costs nothing.
 */

import { snap } from "./measure.js";
import { parseRegionCommand } from "./regionOps.js";

/** One relative step, matching regionOps' REGION_SCALE_STEP. */
export const PART_SCALE_STEP = 1.2;

/**
 * Which PARAMS suffixes count as which axis, best match first. CAD scripts are
 * told to name keys `<part>_<dimension>_mm`, but the dimension word varies, so
 * each axis accepts a few spellings.
 */
export const AXIS_SUFFIXES = {
  length: ["_length_mm", "_len_mm", "_height_mm", "_h_mm", "_depth_mm"],
  width: ["_width_mm", "_w_mm", "_spacing_mm", "_diameter_mm", "_d_mm"],
  thickness: ["_thickness_mm", "_thick_mm", "_t_mm"],
  size: ["_radius_mm", "_r_mm", "_diameter_mm", "_d_mm", "_length_mm", "_width_mm"],
};

// Colour and structural requests belong to other paths entirely. These are
// checked before anything else: "make it red" must never become a resize, and
// "add wings" must reach codegen.
const _NOT_DIMENSIONAL =
  /\b(?:red|blue|green|yellow|gold|black|white|silver|grey|gray|orange|purple|pink|brown|bronze|navy|mint|cream|colou?r|colou?red|paint|painted)\b|\b(?:add|give|put|stick|attach|remove|delete)\b|\bhole\b|\bloop\b|\bsmooth\b|\bflatten\b/i;

// "5 mm longer" / "by 2 cm" — a relative amount.
const _DELTA_RE =
  /\b(?:by\s+)?(\d+(?:\.\d+)?)\s*(mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?)\b/i;
// "20 mm long" / "8 cm tall" — an absolute target, so the unit is followed by
// the adjective rather than a comparative.
const _ABSOLUTE_RE =
  /\b(\d+(?:\.\d+)?)\s*(mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?)\s+(long|tall|high|wide|deep|thick)\b/i;

const _AXIS_WORDS = [
  [/\b(longer|taller|higher|lengthen)\b/i, "length", 1],
  [/\b(shorter|lower|shorten)\b/i, "length", -1],
  [/\b(wider|widen)\b/i, "width", 1],
  [/\b(narrower|narrow)\b/i, "width", -1],
  [/\b(thicker|fatter)\b/i, "thickness", 1],
  [/\b(thinner|slimmer)\b/i, "thickness", -1],
  [/\b(deeper)\b/i, "length", 1],
  [/\b(bigger|larger)\b/i, "size", 1],
  [/\b(smaller)\b/i, "size", -1],
];

const _ABS_ADJ_AXIS = {
  long: "length", tall: "length", high: "length",
  wide: "width", deep: "length", thick: "thickness",
};

function _toMm(value, unit) {
  return /^c/i.test(unit) ? value * 10 : value;
}

/**
 * Parse a dimensional utterance.
 *
 * Returns `{ axis, dir, deltaMm?, absoluteMm? }`, or null when the text is not
 * a dimension change at all (a colour, an addition, or anything unrecognised).
 */
export function parseDimensionCommand(text) {
  const t = (text || "").toLowerCase().trim();
  if (!t || _NOT_DIMENSIONAL.test(t)) return null;

  const abs = t.match(_ABSOLUTE_RE);
  if (abs) {
    return {
      axis: _ABS_ADJ_AXIS[abs[3].toLowerCase()] || "size",
      dir: 1,
      absoluteMm: _toMm(parseFloat(abs[1]), abs[2]),
    };
  }

  for (const [re, axis, dir] of _AXIS_WORDS) {
    if (re.test(t)) {
      const delta = t.match(_DELTA_RE);
      const cmd = { axis, dir };
      if (delta) cmd.deltaMm = _toMm(parseFloat(delta[1]), delta[2]);
      return cmd;
    }
  }
  return null;
}

/**
 * The PARAMS key this part+axis means, or null.
 *
 * Tries the part's own name first, then the base name shared by mirrored or
 * repeated parts ("ear_l" and "ear_r" both read "ear_..."), matching
 * cad.params.params_for_part.
 */
export function resolveParamKey(params, part, axis) {
  if (!params || !part) return null;
  const suffixes = AXIS_SUFFIXES[axis];
  if (!suffixes) return null;

  // "wheel_fl" -> ["wheel_fl", "wheel"]; "ear_l" -> ["ear_l", "ear"].
  const bases = [part];
  const cut = part.lastIndexOf("_");
  if (cut > 0) bases.push(part.slice(0, cut));

  for (const base of bases) {
    for (const suffix of suffixes) {
      const key = base + suffix;
      if (Object.prototype.hasOwnProperty.call(params, key)) return key;
    }
  }
  return null;
}

/**
 * The whole decision in one call: which key changes, from what, to what.
 *
 * Returns null whenever this should NOT be handled locally — no part, no
 * matching parameter, not a dimensional utterance, or a result that would not
 * be a positive size. The caller falls through to the server on null.
 */
export function planPartEdit(params, part, text) {
  const cmd = parseDimensionCommand(text);
  if (!cmd || !part) return null;

  const key = resolveParamKey(params, part, cmd.axis);
  if (!key) return null;

  const from = Number(params[key]);
  if (!Number.isFinite(from) || from <= 0) return null;

  let to;
  if (cmd.absoluteMm !== undefined) {
    to = cmd.absoluteMm;
  } else if (cmd.deltaMm !== undefined) {
    to = from + cmd.dir * cmd.deltaMm;
  } else {
    to = cmd.dir > 0 ? from * PART_SCALE_STEP : from / PART_SCALE_STEP;
  }

  to = snap(to, 1);
  if (!Number.isFinite(to) || to <= 0) return null;
  if (to === from) return null;
  return { key, from, to };
}

/**
 * Does this utterance only make sense against a selected part?
 *
 * A region op or a dimensional change needs something pointed at. Before this
 * existed, saying one with no active selection fell through to the LLM and
 * quietly did something else -- and because the selection is cleared on every
 * model swap, the *second* such command in a row always hit that path. The
 * caller uses this to say "point at a part first" instead.
 */
export function needsSelection(text) {
  const t = (text || "").toLowerCase().trim();
  if (!t) return false;
  if (parseRegionCommand(t)) return true;
  return parseDimensionCommand(t) !== null;
}
