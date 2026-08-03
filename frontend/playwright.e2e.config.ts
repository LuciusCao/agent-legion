import { defineConfig, devices } from '@playwright/test'

/**
 * Deterministic browser smoke E2E (Phase 4A).
 *
 * Specs live in `e2e/` and run against a real backend + vite preview server
 * booted by `scripts/e2e/run_browser_smoke.py`, which injects E2E_BASE_URL.
 * Kept separate from the five-minute `stress/` suite (playwright.config.ts).
 *
 * PR/push runs Chromium only; the nightly job sets E2E_BROWSERS (comma
 * separated) to opt into extra engines, e.g. E2E_BROWSERS=chromium,firefox,webkit.
 */
const ALL_PROJECTS = [
  { name: 'chromium', use: { ...devices['Desktop Chromium'] } },
  // Bootstrap consumes the one-time first-admin slot, so the setup flow runs
  // on Chromium only; extra engines cover the workspace/job flows.
  {
    name: 'firefox',
    use: { ...devices['Desktop Firefox'] },
    grepInvert: /bootstrap 首个管理员/,
  },
  {
    name: 'webkit',
    use: { ...devices['Desktop Safari'] },
    grepInvert: /bootstrap 首个管理员/,
  },
]
const enabledBrowsers = (
  process.env.E2E_BROWSERS?.split(',').map((name) => name.trim()) ?? [
    'chromium',
  ]
).filter(Boolean)
const projects = ALL_PROJECTS.filter((project) =>
  enabledBrowsers.includes(project.name)
)

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  outputDir: './e2e-results/test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects,
})
