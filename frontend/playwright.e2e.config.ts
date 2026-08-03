import { defineConfig, devices } from '@playwright/test'

/**
 * Deterministic browser smoke E2E (Phase 4A).
 *
 * Specs live in `e2e/` and run against a real backend + vite preview server
 * booted by `scripts/e2e/run_browser_smoke.py`, which injects E2E_BASE_URL.
 * Kept separate from the five-minute `stress/` suite (playwright.config.ts).
 */
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
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chromium'] },
    },
  ],
})
