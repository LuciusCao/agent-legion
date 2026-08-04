import { expect, type Page } from '@playwright/test'

// Deterministic admin account shared by the smoke specs. The runner resets
// the E2E database before each run, so the first spec bootstraps and later
// specs just log in.
export const ADMIN = {
  username: 'e2e-admin',
  password: 'e2e-admin-password-1',
  displayName: 'E2E Admin',
} as const

// Mutating API calls need the same custom CSRF header the frontend api()
// layer sends (cookie session + custom header, see src/api/requestAuth.ts).
const CSRF_HEADERS = { 'x-agent-legion-request': '1' }

/**
 * Establish an authenticated session without driving the UI: bootstrap the
 * first admin when the server is fresh, otherwise log in with the shared
 * credentials. Cookies land in the browser context via `page.request`.
 */
export async function ensureAdminSession(page: Page): Promise<void> {
  const statusResponse = await page.request.get('/api/auth/bootstrap')
  expect(statusResponse.ok()).toBeTruthy()
  const status = (await statusResponse.json()) as { available: boolean }
  if (status.available) {
    const response = await page.request.post('/api/auth/bootstrap', {
      headers: CSRF_HEADERS,
      data: {
        username: ADMIN.username,
        password: ADMIN.password,
        display_name: ADMIN.displayName,
      },
    })
    expect(response.ok()).toBeTruthy()
    return
  }
  const response = await page.request.post('/api/auth/login', {
    headers: CSRF_HEADERS,
    data: { username: ADMIN.username, password: ADMIN.password },
  })
  expect(response.ok()).toBeTruthy()
}
