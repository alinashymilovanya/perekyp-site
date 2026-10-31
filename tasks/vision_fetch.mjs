import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const token = process.env.VISION_API_TOKEN;
const folderId = process.env.VISION_FOLDER_ID;
const profileId = process.env.VISION_PROFILE_ID;
const searchUrl = process.env.VISION_SEARCH_URL;
const maxListings = Number.parseInt(process.env.VISION_MAX_LISTINGS || "50", 10);
const playwrightPath = process.env.VISION_PLAYWRIGHT_PATH || "playwright";

if (!token || !folderId || !profileId || !searchUrl || !Number.isInteger(maxListings) || maxListings < 1) {
  throw new Error("Vision configuration is incomplete");
}

async function localApi(path) {
  const response = await fetch(`http://127.0.0.1:3030${path}`, {
    headers: { "X-Token": token },
  });
  if (!response.ok) throw new Error(`Vision API returned ${response.status}`);
  return response.json();
}

async function profilePort() {
  const running = await localApi("/list");
  const profile = (running.profiles || []).find((item) => item.profile_id === profileId);
  if (profile?.port) return profile.port;
  const started = await localApi(`/start/${folderId}/${profileId}`);
  if (!started.port) throw new Error("Vision did not return a DevTools port");
  return started.port;
}

const { chromium } = require(playwrightPath);
const port = await profilePort();
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const context = browser.contexts()[0];
const page = context.pages()[0] || await context.newPage();

await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(2500);

const sort = page.getByText("Сортировка", { exact: true }).first();
if (await sort.count()) {
  await sort.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  const newest = page.getByText("По дате", { exact: true }).first();
  if (await newest.count()) await newest.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1200);
}

const cards = await page.evaluate((limit) => {
  return [...document.querySelectorAll("h2 a[title]")].slice(0, limit).map((a) => {
    const card = a.closest("div[id]");
    return {
      id: card?.id || "",
      title: a.title || a.textContent.trim(),
      url: a.href || "",
      text: card?.innerText || "",
      images: [...(card?.querySelectorAll("img") || [])]
        .map((img) => img.currentSrc || img.src)
        .filter(Boolean)
        .slice(0, 8),
    };
  });
}, maxListings);

process.stdout.write(JSON.stringify(cards));
