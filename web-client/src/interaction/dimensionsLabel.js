/**
 * Floating "W × H × D" label beside the model, in real units.
 *
 * The caller supplies the real size (see measure.js:realSize/realScaleFor);
 * the label never derives it from world bounds, because two-hand zoom changes
 * those without changing the object.
 */
import * as THREE from "three";
import { formatDimensions, labelPosition } from "./measure.js";
import { makeTextSprite } from "./textSprite.js";

const _box = new THREE.Box3();
const _right = new THREE.Vector3();

export function createDimensionsLabel() {
  const { sprite, setText } = makeTextSprite({ widthM: 0.17, aspect: 0.3 });
  const object3d = new THREE.Group();
  object3d.name = "dimensionsLabel";
  object3d.add(sprite);
  object3d.visible = false;
  let hasSize = false;

  function setSize(size) {
    hasSize = true;
    setText([formatDimensions(size), "W × H × D"]);
  }

  /** Park the label beside `model`, to the viewer's right when a camera is given. */
  function follow(model, camera) {
    if (!model || !hasSize) {
      object3d.visible = false;
      return;
    }
    _box.setFromObject(model);
    if (_box.isEmpty()) {
      object3d.visible = false;
      return;
    }
    if (camera) {
      _right.setFromMatrixColumn(camera.matrixWorld, 0);
      _right.y = 0;
      if (_right.lengthSq() < 1e-8) _right.set(1, 0, 0);
      _right.normalize();
    } else {
      _right.set(1, 0, 0);
    }
    // Leave room for half the label's own width.
    const p = labelPosition(_box.min, _box.max, _right, 0.02 + sprite.scale.x / 2);
    object3d.position.set(p.x, p.y, p.z);
    object3d.visible = true;
  }

  function setVisible(v) {
    object3d.visible = v && hasSize;
  }

  return { object3d, setSize, follow, setVisible };
}
