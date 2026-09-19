/**
 * Tape measure: two surface points, a line between them, and the distance.
 *
 * Points are stored in `frame`-local coordinates (the model root), so they
 * ride along when the model is moved or rotated, and the distance ignores the
 * two-hand display zoom (which lives on the frame's scale). `toReal` converts
 * a frame-local distance to real metres when the model is shown at a size
 * other than its real one.
 */
import * as THREE from "three";
import { distanceM, formatLength, tapeNextStep } from "./measure.js";
import { makeTextSprite } from "./textSprite.js";

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();

export function createTapeMeasure(scene, frame = null, { toReal = (d) => d } = {}) {
  const group = new THREE.Group();
  group.name = "tapeMeasure";
  scene.add(group);

  const markerGeo = new THREE.SphereGeometry(0.004, 16, 12);
  const markerMat = new THREE.MeshBasicMaterial({ color: 0xffd54f, depthTest: false });
  const startMarker = new THREE.Mesh(markerGeo, markerMat);
  const endMarker = new THREE.Mesh(markerGeo, markerMat);
  startMarker.renderOrder = endMarker.renderOrder = 998;

  const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
  const line = new THREE.Line(
    lineGeo,
    new THREE.LineBasicMaterial({ color: 0xffd54f, depthTest: false, transparent: true })
  );
  line.renderOrder = 998;

  const { sprite, setText } = makeTextSprite({ widthM: 0.09, aspect: 0.3 });
  group.add(startMarker, endMarker, line, sprite);

  let start = null; // frame-local THREE.Vector3
  let end = null;

  function toLocal(p) {
    const v = new THREE.Vector3(p.x, p.y, p.z);
    if (frame) {
      frame.updateMatrixWorld(true);
      frame.worldToLocal(v);
    }
    return v;
  }

  function toWorld(local, out) {
    out.copy(local);
    if (frame) frame.localToWorld(out);
    return out;
  }

  function distance() {
    return start && end ? toReal(distanceM(start, end)) : null;
  }

  function setStart(p) {
    start = toLocal(p);
    end = null;
    update();
  }

  function setEnd(p) {
    if (!start) {
      setStart(p);
      return;
    }
    end = toLocal(p);
    update();
  }

  /** Place the next endpoint: start, end, then start again. */
  function place(p) {
    if (tapeNextStep(state()) === "start") setStart(p);
    else setEnd(p);
  }

  function clear() {
    start = null;
    end = null;
    update();
  }

  function state() {
    return { hasStart: Boolean(start), hasEnd: Boolean(end) };
  }

  /** Call every frame: re-projects the local points after the model moves. */
  function update() {
    startMarker.visible = Boolean(start);
    endMarker.visible = Boolean(end);
    line.visible = sprite.visible = Boolean(start && end);
    if (start) toWorld(start, startMarker.position);
    if (!end) return;
    setText([formatLength(distance())]);
    toWorld(end, endMarker.position);
    toWorld(start, _a);
    toWorld(end, _b);
    const pos = lineGeo.attributes.position;
    pos.setXYZ(0, _a.x, _a.y, _a.z);
    pos.setXYZ(1, _b.x, _b.y, _b.z);
    pos.needsUpdate = true;
    lineGeo.computeBoundingSphere();
    sprite.position.copy(_a).add(_b).multiplyScalar(0.5);
    sprite.position.y += 0.025;
  }

  update();
  return { setStart, setEnd, place, clear, update, state, distance, object3d: group };
}
