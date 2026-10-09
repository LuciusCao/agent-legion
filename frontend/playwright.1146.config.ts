import { defineConfig, devices } from '@playwright/test'

/**
 * Local demo-acceptance config for the #1146 preview-media screenshots.
 * Not part of any gate: it drives a manually seeded dev stack
 * (scripts/dev_stack.sh + /tmp/1146-demo/seed_demo_1146.py) and writes
 * screenshots to ~/Desktop/0.7.19-ui/1146-preview-media/.
 */
export default defineConfig({
  testDir: './scripts',
  testMatch: /1146-preview-media\.spec\.ts/,
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chromium'] } }],
})
