import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const OUT = path.resolve("docs", "screenshots");
const EXTENSION = path.resolve("extension");
const problems = [];

const EXTRACTION = {
  ok: true,
  platform: "chatgpt",
  title: "数据库事务的 ACID 特性",
  external_id: "chatgpt-demo-0001",
  url: "https://chatgpt.com/c/demo-0001",
  messages: [
    { role: "user", content: "什么是事务的原子性？" },
    {
      role: "assistant",
      content: "原子性指事务中的操作要么全部完成，要么全部不生效。",
    },
  ],
};

const CHATGPT_FIXTURE = `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>数据库事务 - ChatGPT</title></head>
<body>
  <h1 data-testid="conversation-title">数据库事务的 ACID 特性</h1>
  <div data-message-author-role="user">
    <div class="markdown">什么是事务的原子性？</div>
  </div>
  <div data-message-author-role="assistant">
    <div class="markdown">原子性指事务中的操作要么全部完成，要么全部不生效。<button>复制</button></div>
  </div>
</body>
</html>`;

function watch(page, label) {
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`${label} console: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`${label} pageerror: ${error.message}`));
}

async function contentSmoke(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  watch(page, "content");
  await page.addInitScript(() => {
    globalThis.chrome = {
      runtime: {
        onMessage: {
          addListener(handler) {
            globalThis.__lsListener = handler;
          },
        },
      },
    };
  });
  await page.route("https://chatgpt.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: CHATGPT_FIXTURE })
  );
  await page.goto("https://chatgpt.com/c/demo-0001");
  await page.addScriptTag({ path: path.join(EXTENSION, "content.js") });

  const result = await page.evaluate(() => globalThis.__lsExtract());
  if (!result.ok) throw new Error(`内容脚本未提取到消息：${result.error}`);
  if (result.platform !== "chatgpt") throw new Error(`平台识别错误：${result.platform}`);
  if (result.external_id !== "chatgpt-demo-0001") {
    throw new Error(`external_id 不稳定：${result.external_id}`);
  }
  if (result.messages.length !== 2) throw new Error(`消息数错误：${result.messages.length}`);
  if (result.messages[1].content.includes("复制")) {
    throw new Error("按钮文字没有被清理");
  }

  const viaMessage = await page.evaluate(
    () =>
      new Promise((resolve) => {
        globalThis.__lsListener({ type: "LS_EXTRACT" }, null, resolve);
      })
  );
  if (!viaMessage.ok) throw new Error("弹窗消息通道未返回提取结果");
  await context.close();
  console.log("  ✓ content.js 提取 ChatGPT 会话（含按钮清理）");
}

async function popupSmoke(browser) {
  fs.mkdirSync(OUT, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 360, height: 720 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();
  watch(page, "popup");
  await context.addInitScript((extraction) => {
    const store = {};
    globalThis.chrome = {
      storage: {
        local: {
          get: async (defaults) => ({ ...defaults, ...store }),
          set: async (values) => Object.assign(store, values),
        },
      },
      tabs: {
        query: async () => [{ id: 7 }],
        sendMessage: async () => extraction,
      },
      permissions: { request: async () => true },
    };
    globalThis.__sent = null;
    globalThis.fetch = async (url, options) => {
      globalThis.__sent = { url, options };
      return new Response(
        JSON.stringify({
          ok: true,
          batch_id: 1,
          conversations_new: 1,
          conversations_updated: 0,
          messages_new: 2,
          messages_skipped: 0,
          conflicts: 0,
          conversations: [{ id: 1, external_id: extraction.external_id, course_id: 1 }],
          warnings: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    };
  }, EXTRACTION);

  await page.goto(pathToFileURL(path.join(EXTENSION, "popup.html")).href);
  await page.waitForFunction(() => {
    const preview = document.querySelector("#preview");
    return preview && !preview.hidden;
  });
  const title = await page.textContent("#conversation-title");
  if (!title.includes("ACID")) throw new Error(`预览标题错误：${title}`);

  await page.fill("#course-code", "DB");
  await page.fill("#tags", "事务, ACID");
  await page.fill("#token", "ls_smoke_test_token");
  await page.click("#send");
  await page.waitForFunction(() => document.querySelector("#status").dataset.kind === "ok");

  const sent = await page.evaluate(() => globalThis.__sent);
  if (!sent) throw new Error("没有发出 /api/ingest 请求");
  if (!sent.url.endsWith("/api/ingest")) throw new Error(`请求地址错误：${sent.url}`);
  if (!String(sent.options.headers.Authorization).startsWith("Bearer ")) {
    throw new Error("缺少 Bearer 鉴权头");
  }
  const body = JSON.parse(sent.options.body);
  if (body.conversations[0].course_code !== "DB") throw new Error("课程编号未写入请求体");
  if (body.conversations[0].tags.length !== 2) throw new Error("标签未正确拆分");
  if (body.conversations[0].messages.length !== 2) throw new Error("消息未写入请求体");

  const status = await page.textContent("#status");
  await page.screenshot({
    path: path.join(OUT, "21-extension-popup.png"),
    fullPage: true,
    animations: "disabled",
  });
  await context.close();
  console.log(`  ✓ popup 预览、发送与 Bearer 鉴权（${status.trim()}）`);
}

async function run() {
  const browser = await chromium.launch();
  try {
    await contentSmoke(browser);
    await popupSmoke(browser);
  } finally {
    await browser.close();
  }
  if (problems.length) {
    console.error("发现浏览器错误：");
    problems.forEach((item) => console.error(`  - ${item}`));
    process.exitCode = 1;
    return;
  }
  console.log("扩展冒烟通过，无控制台错误");
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
