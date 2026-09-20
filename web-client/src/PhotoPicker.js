/**
 * Floating photo carousel in AR — HUD-styled to match the rest of Percy.
 * Pinch-drag sideways to swipe. Pinch-release without moving to pick.
 *
 * Each card is a group: dark backdrop, aspect-correct photo, a thin accent
 * outline, corner brackets (brighter on whichever card is centered), and a
 * name label. Cards appear immediately with placeholders; photos stream in
 * afterward instead of blocking the whole picker on the slowest download.
 */

import * as THREE from "three";

const CARD_W = 0.28;
const CARD_H = 0.28;
const GAP = 0.065;
const SWIPE_M = 0.03;
const PLACE_DIST = 0.75;

const PHOTO_PAD = 0.014;
const FRAME_PAD = 0.006;
const BRACKET_LEN = 0.05;

const ACCENT = 0x4f8cff; // matches the app's --accent / voice idle color
const ACCENT_BRIGHT = 0x8ec4ff;
const BACKDROP = 0x0a0e18;

const INTRO_DURATION_MS = 320;
const INTRO_STAGGER_MS = 55;

function roundedRectPath(ctx, x, y, w, h, r) {
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Canvas-rendered label with manual letter-spacing (canvas has no tracking API). */
function renderLabelCanvas(text, opts = {}) {
  const {
    width = 512,
    height = 100,
    fontPx = 30,
    color = "#eaf3ff",
    tracking = 2,
    pill = true,
  } = opts;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");

  if (pill) {
    roundedRectPath(ctx, 10, 8, width - 20, height - 16, (height - 16) / 2);
    ctx.fillStyle = "rgba(8, 12, 20, 0.66)";
    ctx.fill();
    ctx.strokeStyle = "rgba(79, 140, 255, 0.45)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  ctx.font = `600 ${fontPx}px "Segoe UI", system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  const chars = [...text];
  let total = 0;
  const widths = chars.map((ch) => {
    const w = ctx.measureText(ch).width;
    total += w + tracking;
    return w;
  });
  total -= tracking;
  let x = (width - total) / 2;
  const y = height / 2 + 1;
  ctx.fillStyle = color;
  chars.forEach((ch, i) => {
    ctx.fillText(ch, x, y);
    x += widths[i] + tracking;
  });
  return canvas;
}

function makeTextPlane(text, { targetHeight, ...opts }) {
  const canvas = renderLabelCanvas(text, opts);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const aspect = canvas.width / canvas.height;
  const h = targetHeight;
  const w = h * aspect;
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(w, h),
    new THREE.MeshBasicMaterial({
      map: tex,
      transparent: true,
      side: THREE.DoubleSide,
      depthWrite: false,
    })
  );
  return mesh;
}

function cleanPhotoName(raw) {
  const stem = (raw || "photo").replace(/\.[a-z0-9]+$/i, "");
  const spaced = stem.replace(/[_-]+/g, " ").trim();
  const upper = spaced.toUpperCase() || "PHOTO";
  return upper.length > 20 ? `${upper.slice(0, 19)}…` : upper;
}

function frameGeometry(w, h) {
  const hw = w / 2;
  const hh = h / 2;
  return new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-hw, -hh, 0),
    new THREE.Vector3(hw, -hh, 0),
    new THREE.Vector3(hw, hh, 0),
    new THREE.Vector3(-hw, hh, 0),
  ]);
}

function bracketGeometry(w, h, armLen) {
  const hw = w / 2;
  const hh = h / 2;
  const corners = [
    [-hw, hh],
    [hw, hh],
    [hw, -hh],
    [-hw, -hh],
  ];
  const pts = [];
  for (const [cx, cy] of corners) {
    const dx = cx < 0 ? 1 : -1;
    const dy = cy < 0 ? 1 : -1;
    pts.push(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx + dx * armLen, cy, 0));
    pts.push(new THREE.Vector3(cx, cy, 0), new THREE.Vector3(cx, cy + dy * armLen, 0));
  }
  return new THREE.BufferGeometry().setFromPoints(pts);
}

function disposeObject(obj) {
  obj.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => {
        m.map?.dispose();
        m.dispose();
      });
    }
  });
}

export class PhotoPicker {
  constructor(scene, getCamera) {
    this.scene = scene;
    this.getCamera = getCamera;
    this.group = new THREE.Group();
    this.group.visible = false;
    scene.add(this.group);

    this.candidates = [];
    this.cards = [];
    this._parts = [];
    this._header = null;
    this._counter = null;
    this._counterIndex = -1;
    this._generation = 0;
    this._introStart = null;

    this.offset = 0;
    this.velocity = 0;
    this._pinchStart = null;
    this._startOffset = 0;
    this._moved = 0;
    this._startLateral = 0;
    this.busy = false;
  }

  get isOpen() {
    return this.group.visible && this.cards.length > 0 && !this.busy;
  }

  async show(candidates, renderer) {
    this.clear();
    this.candidates = candidates || [];
    if (!this.candidates.length) return;

    this._generation += 1;
    const generation = this._generation;

    this.candidates.forEach((c, i) => {
      const parts = this._buildCard(c, i);
      this.group.add(parts.group);
      this.cards.push(parts.group);
      this._parts.push(parts);
    });

    this._buildHeader();

    this.offset = 0;
    this.velocity = 0;
    this._introStart = performance.now();
    this._layout();
    this._placeInFront(renderer);
    this.group.visible = true;

    // Cards render immediately with placeholders — stream photos in without
    // blocking the picker on whichever download is slowest.
    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin("anonymous");
    this.candidates.forEach((c, i) => {
      loader.load(
        c.image_url || c.preview_url,
        (tex) => {
          if (generation !== this._generation) return; // picker moved on
          this._applyTexture(i, tex);
        },
        undefined,
        () => {} // leave the placeholder tone on failure
      );
    });
  }

  _buildCard(candidate) {
    const group = new THREE.Group();
    group.userData.fileId = candidate.id;

    const backdrop = new THREE.Mesh(
      new THREE.PlaneGeometry(CARD_W, CARD_H),
      new THREE.MeshBasicMaterial({
        color: BACKDROP,
        transparent: true,
        opacity: 0.6,
        side: THREE.DoubleSide,
      })
    );
    backdrop.position.z = -0.004;
    group.add(backdrop);

    const photo = new THREE.Mesh(
      new THREE.PlaneGeometry(CARD_W - PHOTO_PAD * 2, CARD_H - PHOTO_PAD * 2),
      new THREE.MeshBasicMaterial({
        color: 0x16202e,
        transparent: true,
        opacity: 0.0,
        side: THREE.DoubleSide,
      })
    );
    group.add(photo);

    const frame = new THREE.LineLoop(
      frameGeometry(CARD_W + FRAME_PAD, CARD_H + FRAME_PAD),
      new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.35 })
    );
    frame.position.z = 0.001;
    group.add(frame);

    const brackets = new THREE.LineSegments(
      bracketGeometry(CARD_W + FRAME_PAD * 2.4, CARD_H + FRAME_PAD * 2.4, BRACKET_LEN),
      new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.45 })
    );
    brackets.position.z = 0.0015;
    group.add(brackets);

    const label = makeTextPlane(cleanPhotoName(candidate.name), {
      targetHeight: 0.05,
      fontPx: 30,
    });
    label.position.set(0, -(CARD_H / 2) - 0.045, 0.001);
    group.add(label);

    return { group, backdrop, photo, frame, brackets, label };
  }

  _buildHeader() {
    this._header = makeTextPlane("SELECT A REFERENCE", {
      targetHeight: 0.06,
      fontPx: 36,
      tracking: 6,
      color: "#bcd6ff",
      pill: false,
    });
    this._header.position.set(0, CARD_H / 2 + 0.11, 0);
    this.group.add(this._header);
  }

  _updateCounter(index) {
    if (this._counterIndex === index || this.candidates.length < 2) return;
    this._counterIndex = index;
    if (this._counter) {
      disposeObject(this._counter);
      this.group.remove(this._counter);
    }
    this._counter = makeTextPlane(`${index + 1} / ${this.candidates.length}`, {
      targetHeight: 0.042,
      fontPx: 32,
      tracking: 3,
      color: "#8ec4ff",
    });
    this._counter.position.set(0, CARD_H / 2 + 0.05, 0.001);
    this.group.add(this._counter);
  }

  _applyTexture(index, tex) {
    const parts = this._parts[index];
    if (!parts) return;
    tex.colorSpace = THREE.SRGBColorSpace;
    const iw = tex.image?.width || 1;
    const ih = tex.image?.height || 1;
    const boxW = CARD_W - PHOTO_PAD * 2;
    const boxH = CARD_H - PHOTO_PAD * 2;
    const scale = Math.min(boxW / iw, boxH / ih);

    parts.photo.geometry.dispose();
    parts.photo.geometry = new THREE.PlaneGeometry(iw * scale, ih * scale);
    parts.photo.material.map = tex;
    parts.photo.material.color.set(0xffffff);
    parts.photo.material.opacity = 1;
    parts.photo.material.needsUpdate = true;
  }

  _introEase(index) {
    if (this._introStart == null) return 1;
    const elapsed = performance.now() - this._introStart - index * INTRO_STAGGER_MS;
    if (elapsed <= 0) return 0;
    if (elapsed >= INTRO_DURATION_MS) return 1;
    const p = elapsed / INTRO_DURATION_MS;
    return 1 - Math.pow(1 - p, 3);
  }

  _applyFocusStyle(index, focus) {
    const parts = this._parts[index];
    if (!parts) return;
    const t = Math.max(0, Math.min(1, focus));
    const centered = t > 0.9;
    const pulse = centered ? 0.08 * Math.sin(performance.now() * 0.004) : 0;
    parts.frame.material.opacity = 0.3 + 0.5 * t;
    parts.brackets.material.opacity = Math.max(0, 0.4 + 0.55 * t + pulse);
    const color = centered ? ACCENT_BRIGHT : ACCENT;
    parts.frame.material.color.setHex(color);
    parts.brackets.material.color.setHex(color);
  }

  keepOnly(fileId) {
    for (const card of this.cards) {
      if (card.userData.fileId !== fileId) {
        card.visible = false;
      }
    }
    if (this._header) this._header.visible = false;
    if (this._counter) this._counter.visible = false;
    this.offset = 0;
    this.velocity = 0;
    this._layout();
  }

  hide() {
    this.clear();
    this.group.visible = false;
  }

  clear() {
    for (const card of this.cards) {
      disposeObject(card);
      this.group.remove(card);
    }
    if (this._header) {
      disposeObject(this._header);
      this.group.remove(this._header);
      this._header = null;
    }
    if (this._counter) {
      disposeObject(this._counter);
      this.group.remove(this._counter);
      this._counter = null;
    }
    this.cards = [];
    this._parts = [];
    this.candidates = [];
    this.offset = 0;
    this.velocity = 0;
    this._pinchStart = null;
    this.busy = false;
    this._introStart = null;
    this._counterIndex = -1;
  }

  beginPinch(worldPos) {
    this._pinchStart = worldPos.clone();
    this._startOffset = this.offset;
    this._startLateral = this._lateral(worldPos);
    this._moved = 0;
    this.velocity = 0;
  }

  movePinch(worldPos) {
    if (!this._pinchStart) return;
    const dx = this._lateral(worldPos) - this._startLateral;
    this._moved = Math.max(this._moved, Math.abs(dx));
    this.offset = this._startOffset + dx;
    this._layout();
  }

  endPinch(worldPos) {
    if (!this._pinchStart) return null;
    const dx = this._lateral(worldPos) - this._startLateral;
    this._moved = Math.max(this._moved, Math.abs(dx));
    this._pinchStart = null;
    if (this._moved < SWIPE_M) {
      return this._nearest(worldPos);
    }
    this.velocity = dx * 4;
    return null;
  }

  _lateral(worldPos) {
    const cam = this.getCamera();
    const q = new THREE.Quaternion();
    cam.getWorldQuaternion(q);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(q);
    right.y = 0;
    if (right.lengthSq() < 1e-6) return worldPos.x;
    right.normalize();
    return worldPos.dot(right);
  }

  tick(dt) {
    if (!this.group.visible || this.cards.length === 0) return;
    if (this._pinchStart) return;
    if (Math.abs(this.velocity) > 0.001) {
      this.offset += this.velocity * dt;
      this.velocity *= 0.92;
      this._clamp();
    }
    this._layout();
  }

  _nearest(worldPos) {
    let best = null;
    let bestD = Infinity;
    for (const card of this.cards) {
      if (!card.visible) continue;
      const p = new THREE.Vector3();
      card.getWorldPosition(p);
      const d = p.distanceTo(worldPos);
      if (d < bestD) {
        bestD = d;
        best = card;
      }
    }
    if (best && bestD < 0.45) return best.userData.fileId;
    if (this.cards.length === 1) return this.cards[0].userData.fileId;
    let centered = this.cards[0];
    let minAbs = Infinity;
    for (const card of this.cards) {
      const a = Math.abs(card.position.x);
      if (a < minAbs) {
        minAbs = a;
        centered = card;
      }
    }
    return centered.userData.fileId;
  }

  _layout() {
    const step = CARD_W + GAP;
    const vis = this.cards.filter((c) => c.visible);
    let bestFocus = -1;
    let bestIndex = 0;
    vis.forEach((card, i) => {
      const x = (i - (vis.length - 1) / 2) * step + this.offset;
      card.position.set(x, 0, 0);
      const focus = 1 - Math.min(1, Math.abs(x) / (step * 1.4));
      const index = this.cards.indexOf(card);
      const ease = this._introEase(index);
      card.scale.setScalar((0.9 + 0.14 * focus) * ease);
      this._applyFocusStyle(index, focus);
      if (focus > bestFocus) {
        bestFocus = focus;
        bestIndex = index;
      }
    });
    this._updateCounter(bestIndex);
  }

  _clamp() {
    const vis = this.cards.filter((c) => c.visible).length;
    if (vis <= 1) {
      this.offset = 0;
      return;
    }
    const span = ((vis - 1) / 2) * (CARD_W + GAP);
    this.offset = Math.max(-span, Math.min(span, this.offset));
  }

  _placeInFront(renderer) {
    const cam = renderer?.xr?.isPresenting ? renderer.xr.getCamera() : this.getCamera();
    cam.updateMatrixWorld(true);
    const pos = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    cam.getWorldPosition(pos);
    cam.getWorldQuaternion(quat);
    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(quat).normalize();
    this.group.position.copy(pos).addScaledVector(forward, PLACE_DIST);
    this.group.position.y = pos.y - 0.05;
    this.group.quaternion.copy(quat);
  }
}
