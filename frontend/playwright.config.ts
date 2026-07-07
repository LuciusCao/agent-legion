import { defineConfig, devices } from '@playwright/test'

/**
 * Minimal Playwright configuration for frontend stress tests.
 *
 * Stress specs live in the `stress/` directory and are invoked via
 * `npm run stress:workspace`. The default browser is Chromium and tests run
 * against the URL supplied by the E2E runner through environment variables.
 */
export default defineConfig({
  testDir: './stress',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.STRESS_BASE_URL || 'http://127.0.0.1:8000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chromium'] },
    },
  ],
})
