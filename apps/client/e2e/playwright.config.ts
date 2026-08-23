import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * The web E2E gate (PROMPT session S3).
 *
 * The browser talks to one origin: the harness serves the exported web app and
 * forwards `/api/*` into the real service in-process, which is the same shape
 * the SPEC §12 deployment has (Caddy in front of the service). So no CORS
 * policy has to be invented for a test, and the app runs against real routers,
 * a real Postgres and a real book on disk.
 *
 * The model provider — and only the provider — is scripted, per D-MLP-34: the
 * browser path must be reproducible and free, and model *behaviour* is
 * measured by the live trial suite in `apps/service/trials`, not here.
 */
const repoRoot = path.resolve(__dirname, "../../..");
const PORT = 8099;

export default defineConfig({
  testDir: path.join(__dirname, "web"),
  outputDir: path.join(__dirname, ".artifacts"),
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run python apps/client/e2e/harness/server.py --port ${PORT}`,
    cwd: repoRoot,
    url: `http://127.0.0.1:${PORT}/__control/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
