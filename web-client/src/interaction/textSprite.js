/**
 * A camera-facing text label drawn to a canvas texture. Readable against
 * passthrough (dark pill, light text) and drawn over the model so it is never
 * hidden inside it.
 */
import * as THREE from "three";

export function makeTextSprite({ widthM = 0.16, px = 512, aspect = 0.3 } = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = px;
  canvas.height = Math.round(px * aspect);
  const ctx = canvas.getContext("2d");
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;

  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(widthM, widthM * aspect, 1);
  sprite.renderOrder = 999;

  let lastKey = "";

  function setText(lines) {
    const key = lines.join("\n");
    if (key === lastKey) return;
    lastKey = key;
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(10, 14, 24, 0.78)";
    const r = h * 0.22;
    ctx.beginPath();
    ctx.roundRect(2, 2, w - 4, h - 4, r);
    ctx.fill();

    const n = Math.max(lines.length, 1);
    const lineH = (h * 0.8) / n;
    ctx.font = `600 ${Math.round(lineH * 0.62)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    lines.forEach((line, i) => {
      ctx.fillStyle = i === 0 ? "#ffffff" : "#9fb4d8";
      ctx.fillText(line, w / 2, h * 0.1 + lineH * (i + 0.5));
    });
    texture.needsUpdate = true;
  }

  function dispose() {
    texture.dispose();
    material.dispose();
  }

  return { sprite, setText, dispose };
}
