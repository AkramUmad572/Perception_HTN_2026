/**
 * Logo HUD while Composio runs. Desktop overlay + world-space chips on Quest.
 */
import * as THREE from "three";
import { BRANDS } from "./brands/index.js";

const PLACE_DIST = 0.75;
const CHIP = 0.11;
const GAP = 0.04;

export class SearchHUD {
  constructor(scene, getCamera) {
    this.scene = scene;
    this.getCamera = getCamera;
    this.group = new THREE.Group();
    this.group.visible = false;
    scene.add(this.group);
    this.meshes = [];
    this.apps = [];
    this.overlay = document.getElementById("searchHud");
    this.captionEl = document.getElementById("searchCaption");
    this.chipsEl = document.getElementById("searchChips");
    this._t = 0;
  }

  get isOpen() {
    return this.group.visible || (this.overlay && !this.overlay.hidden);
  }

  show(apps, caption, renderer) {
    this.clear();
    this.apps = apps || [];
    const inXR = !!renderer?.xr?.isPresenting;
    if (this.captionEl) this.captionEl.textContent = caption || "Looking…";
    if (this.chipsEl) {
      this.chipsEl.innerHTML = "";
      for (const app of this.apps) {
        const brand = BRANDS[app.slug] || {};
        const el = document.createElement("div");
        el.className = `search-chip ${app.status || "queued"}`;
        el.dataset.slug = app.slug;
        el.innerHTML = `<img alt="${brand.label || app.slug}" src="${brand.src || ""}" /><span>${app.label || brand.label || app.slug}</span>`;
        this.chipsEl.appendChild(el);
      }
    }
    if (this.overlay) this.overlay.hidden = inXR || this.apps.length === 0;

    if (inXR) {
      const loader = new THREE.TextureLoader();
      this.apps.forEach((app, i) => {
        const brand = BRANDS[app.slug] || {};
        const mat = new THREE.MeshBasicMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: 0.35,
          side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(new THREE.CircleGeometry(CHIP / 2, 32), mat);
        mesh.userData.slug = app.slug;
        mesh.userData.baseX = (i - (this.apps.length - 1) / 2) * (CHIP + GAP);
        this.group.add(mesh);
        this.meshes.push(mesh);
        if (brand.src) {
          loader.load(brand.src, (tex) => {
            tex.colorSpace = THREE.SRGBColorSpace;
            mesh.material.map = tex;
            mesh.material.needsUpdate = true;
          });
        }
      });
      this._place(renderer);
    }
    this.group.visible = inXR && this.apps.length > 0;
    this.update(this.apps, caption);
  }

  update(apps, caption) {
    if (caption && this.captionEl) this.captionEl.textContent = caption;
    if (apps) this.apps = apps;
    const live = (this.apps || []).find((a) => a.status === "live");
    if (live && this.captionEl && !caption) {
      const brand = BRANDS[live.slug];
      this.captionEl.textContent = `${brand?.label || live.label}…`;
    }
    for (const app of this.apps || []) {
      const el = this.chipsEl?.querySelector(`[data-slug="${app.slug}"]`);
      if (el) el.className = `search-chip ${app.status || "queued"}`;
      const mesh = this.meshes.find((m) => m.userData.slug === app.slug);
      if (mesh) {
        const op = { queued: 0.28, live: 0.95, done: 1, empty: 0.35, error: 0.4 }[app.status] ?? 0.4;
        mesh.material.opacity = op;
        mesh.userData.status = app.status;
      }
    }
  }

  hide() {
    this.clear();
    if (this.overlay) this.overlay.hidden = true;
    this.group.visible = false;
  }

  clear() {
    for (const mesh of this.meshes) {
      mesh.geometry.dispose();
      mesh.material.map?.dispose();
      mesh.material.dispose();
      this.group.remove(mesh);
    }
    this.meshes = [];
    this.apps = [];
  }

  tick(dt) {
    this._t += dt;
    if (!this.group.visible) return;
    for (const mesh of this.meshes) {
      const live = mesh.userData.status === "live";
      const pulse = live ? 1 + 0.08 * Math.sin(this._t * 6) : 1;
      mesh.scale.setScalar(pulse);
      mesh.position.x = mesh.userData.baseX + (live ? Math.sin(this._t * 1.4) * 0.012 : 0);
    }
  }

  _place(renderer) {
    const cam = renderer?.xr?.isPresenting ? renderer.xr.getCamera() : this.getCamera();
    cam.updateMatrixWorld(true);
    const pos = new THREE.Vector3();
    const quat = new THREE.Quaternion();
    cam.getWorldPosition(pos);
    cam.getWorldQuaternion(quat);
    const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(quat).normalize();
    this.group.position.copy(pos).addScaledVector(forward, PLACE_DIST);
    this.group.position.y = pos.y + 0.08;
    this.group.quaternion.copy(quat);
  }
}
