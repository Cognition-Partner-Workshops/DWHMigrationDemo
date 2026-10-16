import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const frontendDir = dirname(fileURLToPath(import.meta.url));
const backendDir = resolve(frontendDir, "../backend");
const runDir = process.env.MIGRAAI_E2E_RUN_DIR ?? mkdtempSync(join(tmpdir(), "migraai-playwright-"));
const devToken = "migraai-phase6-e2e-token";
const viewportProjects = [
  { name: "chromium-1280x720", width: 1280, height: 720, port: 8767 },
  { name: "chromium-1366x768", width: 1366, height: 768, port: 8768 },
  { name: "chromium-1440x900", width: 1440, height: 900, port: 8769 },
] as const;
const baseURLFor = (port: number) => `http://127.0.0.1:${port}`;
const authStatePathFor = (project: string) => join(runDir, `${project}-auth-state.json`);

process.env.MIGRAAI_E2E_DEV_TOKEN = devToken;
process.env.MIGRAAI_E2E_RUN_DIR = runDir;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  outputDir: "test-results",
  globalSetup: resolve(frontendDir, "e2e/global-setup.ts"),
  use: {
    baseURL: baseURLFor(viewportProjects[0].port),
    browserName: "chromium",
    headless: true,
    permissions: ["clipboard-read", "clipboard-write"],
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: viewportProjects.map(({ name, width, height, port }) => ({
    name,
    use: {
      baseURL: baseURLFor(port),
      viewport: { width, height },
      storageState: authStatePathFor(name),
    },
  })),
  webServer: viewportProjects.map(({ name, port }) => ({
    command: `uv run --project . --group dev python -m migraai --no-browser --port ${port} --web-dir ../frontend/dist`,
    cwd: backendDir,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      MIGRAAI_DATA_DIR: join(runDir, name, "data"),
      MIGRAAI_DEV_TOKEN: devToken,
      MIGRAAI_DEVIN_PAT: "",
      MIGRAAI_EMBEDDER: "hash",
      MIGRAAI_TEST_MOCK_DEVIN: "1",
      MIGRAAI_TEST_MOCK_SESSION_DELAY_S: "0.2",
      MIGRAAI_TEST_MOCK_SESSION_RESPONSE_DELAY_S: "1.25",
    },
  })),
});
