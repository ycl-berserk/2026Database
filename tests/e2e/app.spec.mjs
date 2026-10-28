import { expect, test } from "@playwright/test";

const ACCOUNTS = {
  alice: { username: "alice", password: "Alice123!" },
  bob: { username: "bob", password: "Bob123!" },
  admin: { username: "admin", password: "Admin123!" },
};

function trackErrors(page, { allow = () => false } = {}) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (allow(message.text())) return;
    errors.push(`console: ${message.text()}`);
  });
  return errors;
}

async function login(page, key) {
  const account = ACCOUNTS[key];
  await page.goto("/login");
  await page.getByLabel("用户名").fill(account.username);
  await page.getByLabel("密码").fill(account.password);
  await Promise.all([
    page.waitForURL("**/dashboard"),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
}

test("未登录访问概览会跳转到登录页", async ({ page }) => {
  const errors = trackErrors(page);
  const response = await page.goto("/dashboard");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "登录学习空间" })).toBeVisible();
  await expect(page.locator("[data-demo-login]")).toHaveCount(3);
  expect(errors).toEqual([]);
});

test("学生登录后看到学习概览与近期会话", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "学习概览" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近会话" })).toBeVisible();
  await expect(page.locator(".record-list a").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("课程详情展示成员与课程会话", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await page.goto("/courses");
  await expect(page.getByRole("heading", { name: "课程", exact: true })).toBeVisible();
  await page.goto("/courses/1");
  await expect(page.getByRole("heading", { name: "课程成员" })).toBeVisible();
  await expect(page.getByText("@alice").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "课程会话" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("会话详情渲染消息，全局搜索返回结果", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await page.goto("/conversations/7");
  await expect(page.locator(".msg").first()).toBeVisible();
  expect(await page.locator(".msg").count()).toBeGreaterThan(0);

  await page.keyboard.press("Control+k");
  const dialog = page.locator("dialog[open]");
  await expect(dialog).toBeVisible();
  await page.locator("[data-search-input]").fill("事务");
  await Promise.all([
    page.waitForURL(/\/search\?q=/),
    dialog.getByRole("button", { name: "搜索" }).click(),
  ]);
  await expect(page.getByRole("heading", { name: "全局搜索" })).toBeVisible();
  await expect(page.locator(".search-results a").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("会话列表用游标翻页且可返回上一页", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await page.goto("/conversations");
  expect(await page.locator(".conv-card").count()).toBe(10);
  const firstTitle = await page.locator(".conv-card .conv-title").first().innerText();

  await page.locator('nav.pagination a[rel="next"]').click();
  await expect(page).toHaveURL(/[?&]after=/);
  expect(await page.locator(".conv-card").count()).toBe(10);
  expect(await page.locator(".conv-card .conv-title").first().innerText()).not.toBe(
    firstTitle,
  );

  await page.locator('nav.pagination a[rel="prev"]').click();
  await expect(page).toHaveURL(/[?&]before=/);
  expect(await page.locator(".conv-card .conv-title").first().innerText()).toBe(
    firstTitle,
  );
  expect(errors).toEqual([]);
});

test("粘贴对话可预览解析结果且不直接入库", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await page.goto("/imports?mode=paste");
  await page.locator('textarea[name="text"]').fill(
    "我：MVCC 是什么？\n助手：多版本并发控制，读操作不会阻塞写操作。",
  );
  await Promise.all([
    page.waitForURL("**/imports/preview/**"),
    page.locator('form[action="/imports/paste"] button[type="submit"]').click(),
  ]);
  await expect(page.getByText("已解析 1 个会话")).toBeVisible();
  await expect(page.locator(".mini-stats")).toContainText("2");
  await expect(page.getByRole("button", { name: "确认导入所选会话" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("Text2SQL 示例查询返回表格结果", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "alice");
  await page.goto("/text2sql");
  await page.locator("[data-example]").first().click();
  await Promise.all([
    page.waitForFunction(() => {
      const box = document.querySelector("#t2s-result");
      const meta = document.querySelector("#t2s-meta");
      return Boolean(box && !box.hidden && meta && meta.textContent.includes("行"));
    }),
    page.locator("#t2s-submit").click(),
  ]);
  await expect(page.locator("#t2s-sql")).toContainText("SELECT");
  expect(await page.locator("#t2s-table tbody tr").count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("只读成员访问编辑页得到 403 说明页", async ({ page }) => {
  const errors = trackErrors(page, { allow: (text) => text.includes("403") });
  await login(page, "bob");
  const response = await page.goto("/courses/1/edit");
  expect(response?.status()).toBe(403);
  await expect(page.locator(".error-code")).toHaveText("403");
  await expect(page.getByText("只读成员").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("管理员查看数据库观测并切换执行计划", async ({ page }) => {
  const errors = trackErrors(page);
  await login(page, "admin");
  await expect(page.getByRole("heading", { name: "系统概览" })).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "用户与系统" })).toBeVisible();
  await page.goto("/admin/observability");
  await expect(page.getByRole("heading", { name: "数据库观测" })).toBeVisible();
  await page.selectOption('select[name="plan"]', "due_reviews");
  await Promise.all([
    page.waitForURL(/plan=due_reviews/),
    page.getByRole("button", { name: "查看计划" }).click(),
  ]);
  await expect(page.getByRole("heading", { name: "执行计划" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("手机端布局无横向溢出且注册 manifest", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  const errors = trackErrors(page);
  await login(page, "alice");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.locator('link[rel="manifest"]')).toHaveCount(1);
  await expect(page.locator(".mobile-bar")).toBeVisible();
  expect(errors).toEqual([]);
  await context.close();
});
