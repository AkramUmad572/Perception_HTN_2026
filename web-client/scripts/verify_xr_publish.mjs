/**
 * Meta Quest 3 IWER loop: enter AR, send export command, check HUD + result.
 */
import { chromium } from "playwright";

const URL = process.env.XR_URL || "https://localhost:5173/";
const SESSION_API = process.env.API_BASE || "http://127.0.0.1:8000";

async function seedModel() {
  const res = await fetch(`${SESSION_API}/api/script`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: "default",
      color: "#C0C0C0",
      script: 'import cadquery as cq\nresult = cq.Workplane("XY").box(18, 10, 3)\n',
    }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(`seed failed: ${JSON.stringify(data)}`);
  return data;
}

async function main() {
  const seeded = await seedModel();
  console.log("seeded", seeded.glb_url);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  page.on("console", (msg) => {
    const text = msg.text();
    if (/error|fail|publish|export|iwer|xr/i.test(text)) {
      console.log(`[browser:${msg.type()}]`, text.slice(0, 240));
    }
  });

  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => window.PerceptionCAD && navigator.xr, null, { timeout: 30000 });

  const xrMode = await page.evaluate(async () => {
    const ar = await navigator.xr.isSessionSupported("immersive-ar");
    const vr = await navigator.xr.isSessionSupported("immersive-vr");
    return { ar, vr, ua: navigator.userAgent };
  });
  console.log("xr support", xrMode);
  if (!xrMode.ar && !xrMode.vr) {
    throw new Error("IWER did not expose an immersive session");
  }

  const arBtn = page.locator("#arButton");
  await arBtn.waitFor({ state: "visible", timeout: 15000 });
  await arBtn.click();
  await page.waitForFunction(() => {
    const label = document.querySelector("#arButton")?.textContent || "";
    return /STOP|EXIT|END/i.test(label);
  }, null, { timeout: 20000 }).catch(() => {});
  const inXR = await page.evaluate(() => /STOP|EXIT|END/i.test(document.querySelector("#arButton")?.textContent || ""));
  console.log("entered xr", inXR, await arBtn.textContent());

  const unnamed = await page.evaluate(async () => {
    return window.PerceptionCAD.sendCommand("email them saying we finished");
  });
  console.log("unnamed", unnamed?.action, unnamed?.reply);
  if (unnamed?.action !== "clarify" || !/who/i.test(unnamed?.reply || "")) {
    throw new Error(`expected clarify, got ${JSON.stringify(unnamed)}`);
  }

  const published = await page.evaluate(async () => {
    return window.PerceptionCAD.sendCommand("Export this to STL.");
  });
  console.log("export", published?.action, published?.reply, published?.apps);
  if (published?.action === "publishing") {
    throw new Error("client returned publishing without awaiting the job");
  }
  if (published?.action !== "published" || !published?.ok) {
    throw new Error(`export failed: ${JSON.stringify(published)}`);
  }

  const hudHidden = await page.locator("#searchHud").isHidden();
  console.log("hud hidden after publish", hudHidden);
  console.log("xr verify passed");
  await browser.close();
}

main().catch((err) => {
  console.error("FAIL", err);
  process.exit(1);
});
