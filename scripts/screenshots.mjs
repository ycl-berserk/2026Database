import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const OUT = path.resolve("docs", "screenshots");
const RECORD = process.argv.includes("--record");
const VIEWPORT = { width: 1440, height: 950 };

const ACCOUNTS = {
  alice: { username: "alice", password: "Alice123!" },
  bob: { username: "bob", password: "Bob123!" },
  admin: { username: "admin", password: "Admin123!" },
};

fs.mkdirSync(OUT, { recursive: true });

const problems = [];

function watch(page, label, ignoreConsole) {
  page.on("console", (message) => {
    if (message.type() === "error") {
      if (ignoreConsole && ignoreConsole(message.text())) return;
      const where = message.location()?.url;
      problems.push(`${label} console: ${message.text()}${where ? ` (${where})` : ""}`);
    }
  });
  page.on("pageerror", (error) => {
    problems.push(`${label} pageerror: ${error.message}`);
  });
}

async function settle(page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(120);
}

async function shot(page, name, options = {}) {
  await settle(page);
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, fullPage: true, animations: "disabled", ...options });
  console.log(`  ✓ ${name}`);
}

async function login(browser, key, watchOptions = {}) {
  const account = ACCOUNTS[key];
  if (!account) throw new Error(`未知账号 ${key}`);
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
  const page = await context.newPage();
  watch(page, key, watchOptions.ignoreConsole);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', account.username);
  await page.fill('input[name="password"]', account.password);
  await Promise.all([
    page.waitForURL("**/dashboard"),
    page.click('button[type="submit"]'),
  ]);
  await settle(page);
  return { context, page };
}

async function run() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();

  try {
    // 未登录：登录页
    const anon = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
    const loginPage = await anon.newPage();
    watch(loginPage, "anonymous");
    await loginPage.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await shot(loginPage, "00-login.png");
    await anon.close();

    // alice：主流程
    const { context: aliceCtx, page: alice } = await login(browser, "alice");
    await shot(alice, "01-dashboard.png");

    await alice.goto(`${BASE}/courses`, { waitUntil: "networkidle" });
    await shot(alice, "02-courses.png");

    await alice.goto(`${BASE}/courses/1`, { waitUntil: "networkidle" });
    await shot(alice, "03-course-detail.png");

    await alice.goto(`${BASE}/conversations`, { waitUntil: "networkidle" });
    await shot(alice, "04-conversations.png");

    await alice.goto(`${BASE}/conversations/7`, { waitUntil: "networkidle" });
    await shot(alice, "05-conversation-detail.png");

    await alice.goto(`${BASE}/tags`, { waitUntil: "networkidle" });
    await shot(alice, "06-tags.png");

    await alice.goto(`${BASE}/imports`, { waitUntil: "networkidle" });
    await shot(alice, "07-imports.png");

    // 上传样例 → 预览页；确认前不写入会话/消息
    await alice.setInputFiles('input[type="file"]', path.resolve("data", "samples", "qoder_ml.json"));
    await Promise.all([
      alice.waitForURL("**/imports/preview/**"),
      alice.click('form[action="/imports/preview"] button[type="submit"]'),
    ]);
    await shot(alice, "08-import-preview.png");

    await alice.goto(`${BASE}/annotations`, { waitUntil: "networkidle" });
    await shot(alice, "09-annotations.png");

    await alice.goto(`${BASE}/reviews`, { waitUntil: "networkidle" });
    await shot(alice, "10-reviews.png");

    await alice.goto(`${BASE}/stats`, { waitUntil: "networkidle" });
    await shot(alice, "11-stats.png");

    await alice.goto(`${BASE}/text2sql`, { waitUntil: "networkidle" });
    await shot(alice, "12-text2sql.png");

    const chip = alice.locator("[data-example]").first();
    await chip.click();
    await Promise.all([
      alice.waitForFunction(() => {
        const meta = document.querySelector("#t2s-meta");
        const box = document.querySelector("#t2s-result");
        return box && !box.hidden && meta && meta.textContent.includes("行");
      }),
      alice.click("#t2s-submit"),
    ]);
    await shot(alice, "13-text2sql-result.png");
    await aliceCtx.close();

    // admin：仅聚合数据
    const { context: adminCtx, page: admin } = await login(browser, "admin");
    await shot(admin, "14-admin.png");
    await adminCtx.close();

    // bob：viewer 越权被拒（DB 课程为 viewer）；403 资源错误是预期行为
    const { context: bobCtx, page: bob } = await login(browser, "bob", {
      ignoreConsole: (text) => text.includes("403 (Forbidden)"),
    });
    const response = await bob.goto(`${BASE}/courses/1/edit`, { waitUntil: "networkidle" });
    await shot(bob, "15-permission-denied.png");
    if (!response || response.status() !== 403) {
      problems.push(`bob 越权页状态码异常：${response ? response.status() : "无响应"}`);
    }
    await bobCtx.close();

    // E-R 图：SVG → PNG，便于直接插入报告
    const erContext = await browser.newContext({
      viewport: { width: 1480, height: 1250 },
      deviceScaleFactor: 1,
    });
    const erPage = await erContext.newPage();
    watch(erPage, "er");
    await erPage.goto(pathToFileURL(path.resolve("docs", "er-chen.svg")).href);
    await erPage.waitForTimeout(300);
    await erPage.locator("svg").screenshot({
      path: path.join(OUT, "16-er-chen.png"),
      timeout: 90000,
    });
    console.log("  ✓ 16-er-chen.png");
    await erContext.close();

    await mobilePass(browser);

    if (RECORD) await recordTour(browser);
  } finally {
    await browser.close();
  }

  if (problems.length) {
    console.error("\n发现前端错误：");
    for (const item of problems) console.error(`  - ${item}`);
    process.exitCode = 1;
  } else {
    console.log("\n全部页面截图完成，控制台无报错。");
  }
}

async function mobilePass(browser) {
  const dir = path.join(OUT, "mobile");
  fs.mkdirSync(dir, { recursive: true });

  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  watch(page, "mobile");

  const mobileShot = async (name, options = {}) => {
    await settle(page);
    await page.screenshot({
      path: path.join(dir, name),
      fullPage: true,
      animations: "disabled",
      ...options,
    });
    console.log(`  ✓ mobile/${name}`);
  };

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await mobileShot("00-login.png");

  await page.fill('input[name="username"]', ACCOUNTS.alice.username);
  await page.fill('input[name="password"]', ACCOUNTS.alice.password);
  await Promise.all([
    page.waitForURL("**/dashboard"),
    page.click('button[type="submit"]'),
  ]);

  await mobileShot("01-dashboard.png");

  await page.goto(`${BASE}/conversations`, { waitUntil: "networkidle" });
  await mobileShot("02-conversations.png");

  await page.goto(`${BASE}/courses/1`, { waitUntil: "networkidle" });
  await mobileShot("03-course-detail.png");

  await page.goto(`${BASE}/conversations/7`, { waitUntil: "networkidle" });
  await mobileShot("04-reading.png");

  await page.goto(`${BASE}/imports`, { waitUntil: "networkidle" });
  await mobileShot("05-imports.png");

  await page.goto(`${BASE}/reviews`, { waitUntil: "networkidle" });
  await mobileShot("06-reviews.png");

  await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });
  await page.click(".mobile-bar [data-nav-toggle]");
  await page.waitForTimeout(260);
  await page.screenshot({
    path: path.join(dir, "07-drawer.png"),
    fullPage: false,
    animations: "disabled",
  });
  console.log("  ✓ mobile/07-drawer.png");

  const manifestLinked = await page.evaluate(
    () => Boolean(document.querySelector('link[rel="manifest"]'))
  );
  if (!manifestLinked) problems.push("mobile: 页面缺少 manifest 链接");

  let swReady = false;
  try {
    await page.waitForFunction(
      async () => Boolean(await navigator.serviceWorker.getRegistration()),
      undefined,
      { timeout: 5000 }
    );
    swReady = true;
  } catch {
    swReady = false;
  }
  if (!swReady) problems.push("mobile: Service Worker 未注册（需 localhost 或 HTTPS）");

  await context.close();
}

async function recordTour(browser) {
  const videoDir = path.resolve("docs", "video-tmp");
  fs.mkdirSync(videoDir, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
  });
  const page = await context.newPage();
  watch(page, "record");
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.fill('input[name="username"]', ACCOUNTS.alice.username);
  await page.fill('input[name="password"]', ACCOUNTS.alice.password);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard");
  const stops = [
    "/dashboard",
    "/courses",
    "/conversations",
    "/conversations/7",
    "/imports",
    "/annotations",
    "/stats",
    "/text2sql",
  ];
  for (const stop of stops) {
    await page.goto(BASE + stop, { waitUntil: "networkidle" });
    await page.waitForTimeout(900);
  }
  const video = page.video();
  await context.close();
  if (video) {
    const raw = await video.path();
    const target = path.resolve("docs", "demo.webm");
    fs.copyFileSync(raw, target);
    fs.rmSync(videoDir, { recursive: true, force: true });
    console.log(`  ✓ docs/demo.webm`);
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
