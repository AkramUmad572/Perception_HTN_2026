"""Boolean features on sculpts: drill a hole, add a loop, flatten the base.

All coordinates are model-local GLB units. Sizes are real millimetres, converted
with ``units_per_mm`` (model units per real millimetre), because a sculpt is
unit-normalised and only the session knows its real size.

Appearance survives because the texture is baked to per-vertex colours on load,
and every vertex a boolean produces takes the colour of the nearest vertex of
the input. Booleans run on manifold3d (``engine="manifold"``), which needs a
watertight input, so every feature repairs first.

``BooleanError`` messages are spoken to the user in the headset.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


class BooleanError(ValueError):
    """A boolean feature could not be applied. The message is speakable."""


MSG_CANT_OPEN = "I couldn't open that model to edit it."


def load_mesh(glb_path: Path) -> trimesh.Trimesh:
    """Load a GLB as one mesh with the texture baked to vertex colours."""
    try:
        loaded = trimesh.load(str(glb_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_mesh failed for %s: %s", glb_path, exc)
        raise BooleanError(MSG_CANT_OPEN) from exc

    scene = trimesh.Scene(loaded) if isinstance(loaded, trimesh.Trimesh) else loaded
    if not isinstance(scene, trimesh.Scene):
        raise BooleanError(MSG_CANT_OPEN)

    pieces = []
    # dump() applies each node's transform, so parts land where they are drawn.
    for geom in scene.dump():
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        colors = _baked_colors(geom)
        pieces.append(
            trimesh.Trimesh(
                vertices=np.asarray(geom.vertices),
                faces=np.asarray(geom.faces),
                vertex_colors=colors,
                process=False,
            )
        )
    if not pieces:
        raise BooleanError(MSG_CANT_OPEN)
    return pieces[0] if len(pieces) == 1 else trimesh.util.concatenate(pieces)


def _baked_colors(geom: trimesh.Trimesh) -> np.ndarray:
    """Per-vertex RGBA sampled from whatever visual the geometry carries."""
    try:
        visual = geom.visual
        # ColorVisuals (already per-vertex or per-face) has no to_color();
        # its vertex_colors property converts face colours itself.
        if isinstance(visual, trimesh.visual.ColorVisuals):
            return np.asarray(visual.vertex_colors, dtype=np.uint8)
        return np.asarray(visual.to_color().vertex_colors, dtype=np.uint8)
    except Exception as exc:  # noqa: BLE001
        logger.info("Colour bake failed, using default grey: %s", exc)
        return np.tile(np.array([200, 200, 200, 255], dtype=np.uint8), (len(geom.vertices), 1))


def export_glb(mesh: trimesh.Trimesh, dest: Path) -> None:
    """Write the mesh, with its vertex colours, as a GLB."""
    Path(dest).write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh)))


MSG_EMPTY = "There's no model to edit."
MSG_CANT_REPAIR = (
    "This sculpt has gaps I couldn't close, so I can't cut into it. The model is unchanged."
)
# Open or zero-volume pieces smaller than this share of the faces are treated as
# generator debris and dropped. Anything bigger is real geometry we must not eat.
DEBRIS_FACE_SHARE = 0.05
# Relative weld tolerance (fraction of the bounding diagonal) for seams split by
# float noise.
SEAM_WELD_REL = 1e-6


def repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """
    Return a watertight copy of ``mesh`` with its colours, or raise BooleanError.

    Rungs, cheapest first; each builds on the last:
      1. weld exact duplicates, drop degenerate/duplicate faces, fix winding
      2. weld near-duplicates (seams split by float noise)
      3. cut out faces on non-manifold edges (they become holes)
      4. close every boundary loop with a centroid fan
    Then open or zero-volume pieces are dropped when they are only debris.
    """
    if mesh is None or len(getattr(mesh, "faces", [])) == 0:
        raise BooleanError(MSG_EMPTY)

    work = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces), process=False
    )
    _tidy(work)
    rungs = (_weld_seams, _cut_nonmanifold, _fill_boundary_loops)
    for rung in (None, *rungs):
        if rung is not None:
            work = rung(work)
            _tidy(work)
        solid = _keep_solid_bodies(work)
        if solid is not None:
            return _transfer_colors(solid, _colored(mesh))

    logger.info("repair gave up: %d faces, watertight=%s", len(work.faces), work.is_watertight)
    raise BooleanError(MSG_CANT_REPAIR)


def _colored(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """The mesh as a colour source; meshes without colours get the default."""
    if mesh.visual.kind == "vertex":
        return mesh
    src = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
    src.visual.vertex_colors = _baked_colors(mesh)
    return src


def _tidy(m: trimesh.Trimesh) -> None:
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    if len(m.faces):
        trimesh.repair.fix_normals(m, multibody=True)


def _weld_seams(m: trimesh.Trimesh) -> trimesh.Trimesh:
    diag = float(np.linalg.norm(m.extents)) if len(m.vertices) else 0.0
    if diag <= 0:
        return m
    digits = max(0, int(np.floor(-np.log10(diag * SEAM_WELD_REL))))
    m.merge_vertices(digits_vertex=digits)
    return m


def _cut_nonmanifold(m: trimesh.Trimesh) -> trimesh.Trimesh:
    groups = trimesh.grouping.group_rows(m.edges_sorted)
    bad_edges = [g for g in groups if len(g) > 2]
    if not bad_edges:
        return m
    bad_faces = np.unique(m.edges_face[np.concatenate(bad_edges)])
    keep = np.ones(len(m.faces), dtype=bool)
    keep[bad_faces] = False
    m.update_faces(keep)
    m.remove_unreferenced_vertices()
    return m


def _boundary_loops(m: trimesh.Trimesh) -> list[list[int]]:
    """Closed loops of boundary edges, split at pinch vertices."""
    from collections import defaultdict

    single = trimesh.grouping.group_rows(m.edges_sorted, require_count=1)
    if len(single) == 0:
        return []
    # m.edges keeps each face's winding, so a loop walked along these edges
    # can be fanned shut with the reversed winding.
    succ: dict[int, list[int]] = defaultdict(list)
    for a, b in m.edges[single].tolist():
        succ[a].append(b)

    loops: list[list[int]] = []
    for start in list(succ):
        while succ[start]:
            path = [start]
            cur = start
            while succ[cur]:
                cur = succ[cur].pop()
                if cur in path:
                    # Revisiting a vertex closes a sub-loop; peel it off so a
                    # pinch vertex never gets two fans on the same edge.
                    i = path.index(cur)
                    loops.append(path[i:])
                    path = path[: i + 1]
                else:
                    path.append(cur)
    return [loop for loop in loops if len(loop) >= 3]


def _fill_boundary_loops(m: trimesh.Trimesh) -> trimesh.Trimesh:
    trimesh.repair.fill_holes(m)
    loops = _boundary_loops(m)
    if not loops:
        return m
    verts = [np.asarray(m.vertices)]
    faces = [np.asarray(m.faces)]
    next_index = len(m.vertices)
    for loop in loops:
        verts.append(m.vertices[loop].mean(axis=0, keepdims=True))
        ring = np.asarray(loop)
        faces.append(np.column_stack([np.roll(ring, -1), ring, np.full(len(ring), next_index)]))
        next_index += 1
    return trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(faces), process=False)


def _keep_solid_bodies(m: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """The mesh minus debris if what remains is a proper solid, else None."""
    if len(m.faces) == 0:
        return None
    if m.body_count == 1:
        return m if _is_solid(m, m) else None
    bodies = m.split(only_watertight=False)
    solid = [b for b in bodies if _is_solid(b, m)]
    if not solid:
        return None
    dropped = len(m.faces) - sum(len(b.faces) for b in solid)
    if dropped > DEBRIS_FACE_SHARE * len(m.faces):
        return None
    out = solid[0] if len(solid) == 1 else trimesh.util.concatenate(solid)
    return out if _is_solid(out, m) else None


def _is_solid(part: trimesh.Trimesh, whole: trimesh.Trimesh) -> bool:
    if not (part.is_watertight and part.is_winding_consistent):
        return False
    scale = float(np.prod(np.maximum(whole.extents, 1e-12)))
    return part.volume > 1e-6 * scale


MSG_NO_SIZE = "I don't know this model's real size yet, so I can't size that."
MSG_HOLE_SIZE = "The hole needs a size bigger than zero."
MSG_HOLE_DEPTH = "The hole needs a depth bigger than zero."
MSG_NO_DIRECTION = "I couldn't tell which way to drill."
MSG_CUT_FAILED = "The cut didn't work on this shape. The model is unchanged."
MSG_HOLE_MISSED = "That spot missed the model, so there was nothing to drill."
# Below this relative volume change the boolean did nothing: the tool missed.
MIN_VOLUME_CHANGE = 1e-6
CUTTER_SECTIONS = 48


def drill_hole(
    mesh: trimesh.Trimesh,
    center,
    direction,
    diameter_mm: float,
    units_per_mm: float,
    depth_mm: float | None = None,
) -> trimesh.Trimesh:
    """
    Bore a round hole at ``center`` (a surface point) along ``direction``
    (pointing into the model). ``depth_mm=None`` drills all the way through.
    """
    units = _units(units_per_mm)
    if not _positive(diameter_mm):
        raise BooleanError(MSG_HOLE_SIZE)
    if depth_mm is not None and not _positive(depth_mm):
        raise BooleanError(MSG_HOLE_DEPTH)
    axis = _unit(direction, MSG_NO_DIRECTION)
    c = np.asarray(center, dtype=float)

    solid = repair(mesh)
    radius = diameter_mm / 2.0 * units
    if depth_mm is None:
        reach = 2.0 * float(np.linalg.norm(solid.extents)) + np.linalg.norm(c - solid.centroid)
        segment = [c - axis * reach, c + axis * reach]
    else:
        # Start a little outside the surface so the mouth of the hole is clean.
        lead = max(radius, units)
        segment = [c - axis * lead, c + axis * depth_mm * units]
    cutter = trimesh.creation.cylinder(radius=radius, segment=segment, sections=CUTTER_SECTIONS)

    out = _boolean(solid, cutter, "difference")
    if abs(solid.volume - out.volume) <= MIN_VOLUME_CHANGE * solid.volume:
        raise BooleanError(MSG_HOLE_MISSED)
    return _transfer_colors(out, solid)


def _units(units_per_mm) -> float:
    if not _positive(units_per_mm):
        raise BooleanError(MSG_NO_SIZE)
    return float(units_per_mm)


def _positive(value) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(v) and v > 0)


def _unit(vector, message: str) -> np.ndarray:
    try:
        v = np.asarray(vector, dtype=float).reshape(3)
    except (TypeError, ValueError) as exc:
        raise BooleanError(message) from exc
    n = float(np.linalg.norm(v))
    if not np.isfinite(n) or n < 1e-12:
        raise BooleanError(message)
    return v / n


def _boolean(solid: trimesh.Trimesh, tool: trimesh.Trimesh, op: str) -> trimesh.Trimesh:
    """Run one manifold boolean; any failure becomes a speakable error."""
    try:
        if op == "difference":
            out = solid.difference(tool, engine="manifold")
        else:
            out = solid.union(tool, engine="manifold")
    except Exception as exc:  # noqa: BLE001
        logger.warning("manifold %s failed: %s", op, exc)
        raise BooleanError(MSG_CUT_FAILED) from exc
    if not isinstance(out, trimesh.Trimesh) or len(out.faces) == 0 or not out.is_watertight:
        raise BooleanError(MSG_CUT_FAILED)
    return out


def _transfer_colors(dst: trimesh.Trimesh, src: trimesh.Trimesh) -> trimesh.Trimesh:
    """Give every vertex of dst the colour of the nearest vertex of src."""
    from scipy.spatial import cKDTree

    src_colors = np.asarray(src.visual.vertex_colors, dtype=np.uint8)
    _dist, idx = cKDTree(np.asarray(src.vertices)).query(np.asarray(dst.vertices))
    dst.visual = trimesh.visual.ColorVisuals(mesh=dst, vertex_colors=src_colors[idx])
    return dst
