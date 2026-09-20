/**
 * World-space + desktop cards for email / Notion / other text results.
 */
import * as THREE from "three";
import { BRANDS } from "./brands/index.js";

const CARD_W = 0.48;
const CARD_H = 0.32;
const GAP = 0.04;
const PLACE_DIST = 0.75;
const AVATARS = ["#1a73e8", "#d93025", "#188038", "#e37400", "#9334e6", "#c5221f", "#007b83"];

function avatarColor(name) {
  const s = String(name || "?");
  let n = 0;
  for (let i = 0; i < s.length; i += 1) n = (n + s.charCodeAt(i) * (i + 1)) % AVATARS.length;
  return AVATARS[n];
}

function initial(name) {
  const ch = String(name || "?").trim().charAt(0);
  return (ch || "?").toUpperCase();
}

function icon(path) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
}

const ICONS = {
  archive: "M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM6.24 5h11.52l.81.97H5.44l.8-.97zM5 19V8h14v11H5zm8.45-9h-2.9v3H8l4 4 4-4h-2.55z",
  spam: "M15.73 3H8.27L3 8.27v7.46L8.27 21h7.46L21 15.73V8.27L15.73 3zM19 14.9L14.9 19H9.1L5 14.9V9.1L9.1 5h5.8L19 9.1v5.8zM12 17c.55 0 1-.45 1-1s-.45-1-1-1-1 .45-1 1 .45 1 1 1zm1-3h-2V7h2v7z",
  trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
  unread: "M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z",
  more: "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
  reply: "M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z",
  forward: "M12 8V4l8 8-8 8v-4H4V8z",
};

function isEmailItem(item) {
  return item.kind === "email" || item.source === "gmail";
}

function paintGmailCard(ctx, c, item) {
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = "#EA4335";
  ctx.fillRect(0, 0, c.width, 8);
  ctx.fillStyle = "#1f1f1f";
  ctx.font = "400 44px Roboto, sans-serif";
  wrap(ctx, item.title || "Untitled", 48, 80, 920, 52);
  const who = item.sender_name || item.subtitle || "";
  ctx.fillStyle = avatarColor(who);
  ctx.beginPath();
  ctx.arc(76, 220, 28, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.font = "500 28px Roboto, sans-serif";
  ctx.fillText(initial(who), 67, 230);
  ctx.fillStyle = "#202124";
  ctx.font = "700 28px Roboto, sans-serif";
  ctx.fillText(String(who).slice(0, 36), 124, 214);
  ctx.fillStyle = "#5f6368";
  ctx.font = "400 22px Roboto, sans-serif";
  ctx.fillText("to me", 124, 246);
  if (item.meta) ctx.fillText(String(item.meta).slice(0, 40), 700, 230);
  ctx.fillStyle = "#222";
  ctx.font = "400 28px Roboto, sans-serif";
  wrap(ctx, item.body || "", 48, 320, 920, 38);
}

// Non-Gmail items (calendar events, Notion pages, GitHub issues, ...) get a
// card styled after their own app — brand color + label, not Gmail's "to me"
// inbox chrome, which made every pulled item look like an email regardless
// of where it actually came from.
function paintGenericCard(ctx, c, item) {
  const brand = BRANDS[item.source] || { color: "#5f6368", label: item.source || "" };
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = brand.color;
  ctx.fillRect(0, 0, c.width, 8);
  ctx.fillStyle = brand.color;
  ctx.font = "700 24px Roboto, sans-serif";
  ctx.fillText(brand.label.toUpperCase(), 48, 56);
  ctx.fillStyle = "#1f1f1f";
  ctx.font = "400 44px Roboto, sans-serif";
  wrap(ctx, item.title || "Untitled", 48, 120, 920, 52);
  if (item.subtitle) {
    ctx.fillStyle = "#5f6368";
    ctx.font = "400 26px Roboto, sans-serif";
    ctx.fillText(String(item.subtitle).slice(0, 60), 48, 220);
  }
  ctx.fillStyle = "#222";
  ctx.font = "400 28px Roboto, sans-serif";
  wrap(ctx, item.body || "", 48, 280, 920, 38);
}

function paintCard(item) {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 720;
  const ctx = c.getContext("2d");
  if (isEmailItem(item)) {
    paintGmailCard(ctx, c, item);
  } else {
    paintGenericCard(ctx, c, item);
  }
  return c;
}

function wrap(ctx, text, x, y, maxW, lineH) {
  const words = String(text).split(/\s+/);
  let line = "";
  let rows = 0;
  for (const w of words) {
    const test = line ? `${line} ${w}` : w;
    if (ctx.measureText(test).width > maxW && line) {
      ctx.fillText(line, x, y + rows * lineH);
      line = w;
      rows += 1;
      if (rows > 6) break;
    } else {
      line = test;
    }
  }
  if (rows <= 6 && line) ctx.fillText(line, x, y + rows * lineH);
}

function renderGmail(list) {
  const first = list[0];
  const who = first.sender_name || first.subtitle || "Unknown";
  const logo = BRANDS.gmail?.src || "";
  const suggest = first.suggested_reply
    ? `<div class="gmail-suggest">
        <div class="gmail-suggest-label">Suggested reply</div>
        <div class="gmail-suggest-chip">${escapeHtml(first.suggested_reply)}</div>
      </div>`
    : "";
  const extras = list.slice(1).map((it) => {
    const name = it.sender_name || it.subtitle || "";
    return `<div class="gmail-row">
      <div class="gmail-avatar" style="background:${avatarColor(name)}">${escapeHtml(initial(name))}</div>
      <div class="gmail-row-from">${escapeHtml(name)}</div>
      <div class="gmail-row-snip"><b>${escapeHtml(it.title || "")}</b> — ${escapeHtml(it.body || "")}</div>
      <div class="gmail-row-time">${escapeHtml(it.meta || "")}</div>
    </div>`;
  }).join("");

  return `<div class="gmail-chrome">
      ${logo ? `<img class="gmail-logo" alt="" src="${logo}">` : ""}
      <span class="gmail-chrome-title">Inbox</span>
      <span class="gmail-count">1 of ${list.length}</span>
    </div>
    <div class="gmail-toolbar">
      <span>${icon(ICONS.archive)}</span>
      <span>${icon(ICONS.spam)}</span>
      <span>${icon(ICONS.trash)}</span>
      <span>${icon(ICONS.unread)}</span>
      <span>${icon(ICONS.more)}</span>
    </div>
    <div class="gmail-subject">${escapeHtml(first.title || "(no subject)")}
      <span class="gmail-chip">Inbox ×</span>
    </div>
    <div class="gmail-msg">
      <div class="gmail-who">
        <div class="gmail-avatar" style="background:${avatarColor(who)}">${escapeHtml(initial(who))}</div>
        <div>
          <div class="gmail-from">${escapeHtml(who)}</div>
          <div class="gmail-tome">to me ▾</div>
        </div>
        <div class="gmail-time">${escapeHtml(first.meta || "")}</div>
      </div>
      <p class="gmail-body">${escapeHtml(first.body || "")}</p>
      ${suggest}
      <div class="gmail-actions">
        <span class="gmail-btn">↩ Reply</span>
        <span class="gmail-btn">→ Forward</span>
      </div>
    </div>
    ${extras ? `<div class="gmail-more">${extras}</div>` : ""}`;
}

export class ItemPicker {
  constructor(scene, getCamera) {
    this.scene = scene;
    this.getCamera = getCamera;
    this.group = new THREE.Group();
    this.group.visible = false;
    scene.add(this.group);
    this.overlay = document.getElementById("itemCards");
  }

  show(items, renderer) {
    this.hide();
    const list = items || [];
    const inXR = !!renderer?.xr?.isPresenting;
    const emails = list.filter((it) => isEmailItem(it));
    // Only use the full Gmail inbox chrome when every result actually came
    // from Gmail — a mixed or non-email pull (calendar, Notion, "pick up
    // everywhere") must not silently drop its non-email items just because
    // one email happened to be in the mix.
    const allEmails = list.length > 0 && emails.length === list.length;
    if (this.overlay) {
      this.overlay.innerHTML = "";
      this.overlay.classList.toggle("gmail-window", !inXR && allEmails);
      if (!inXR) {
        if (allEmails) {
          this.overlay.innerHTML = renderGmail(emails);
        } else {
          for (const it of list) {
            const brand = BRANDS[it.source];
            const el = document.createElement("article");
            el.className = "item-card";
            el.innerHTML = `<p class="item-kicker">${escapeHtml(brand?.label || it.subtitle || it.source || "")}</p>
            <h2>${escapeHtml(it.title || "")}</h2>
            <p class="item-body">${escapeHtml(it.body || "")}</p>
            ${it.meta ? `<p class="item-meta">${escapeHtml(it.meta)}</p>` : ""}`;
            this.overlay.appendChild(el);
          }
        }
      }
      this.overlay.hidden = inXR || list.length === 0;
    }
    if (inXR) {
      list.forEach((it, i) => {
        const tex = new THREE.CanvasTexture(paintCard(it));
        tex.colorSpace = THREE.SRGBColorSpace;
        const mesh = new THREE.Mesh(
          new THREE.PlaneGeometry(CARD_W, CARD_H),
          new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide })
        );
        mesh.position.x = (i - (list.length - 1) / 2) * (CARD_W + GAP);
        this.group.add(mesh);
      });
      this._place(renderer);
    }
    this.group.visible = inXR && list.length > 0;
  }

  hide() {
    while (this.group.children.length) {
      const ch = this.group.children[0];
      ch.geometry?.dispose();
      ch.material?.map?.dispose();
      ch.material?.dispose();
      this.group.remove(ch);
    }
    this.group.visible = false;
    if (this.overlay) {
      this.overlay.innerHTML = "";
      this.overlay.classList.remove("gmail-window");
      this.overlay.hidden = true;
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
    this.group.position.y = pos.y - 0.02;
    this.group.quaternion.copy(quat);
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
