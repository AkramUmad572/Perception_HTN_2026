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


def _transfer_colors(dst: trimesh.Trimesh, src: trimesh.Trimesh) -> trimesh.Trimesh:
    """Give every vertex of dst the colour of the nearest vertex of src."""
    from scipy.spatial import cKDTree

    src_colors = np.asarray(src.visual.vertex_colors, dtype=np.uint8)
    _dist, idx = cKDTree(np.asarray(src.vertices)).query(np.asarray(dst.vertices))
    dst.visual = trimesh.visual.ColorVisuals(mesh=dst, vertex_colors=src_colors[idx])
    return dst
