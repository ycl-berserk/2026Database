import { defineConfig } from "@playwright/test";

const baseURL = process.env.BASE_URL || "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "tests/e2e",
  outputDir: "data/verification/playwright",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [
    ["list"],
    ["junit", { outputFile: "data/verification/e2e-junit.xml" }],
  ],
  use: {
    baseURL,
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
