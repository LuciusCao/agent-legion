import { expect, type Page } from '@playwright/test'
import yaml from 'js-yaml'

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

/**
 * Create a workspace seeded from the demo template via the API and return
 * its id. Multi-browser smoke runs share one database: CJK names collapse
 * to the same transliterated slug, so later engines get `_2`/`_3` suffixed
 * ids — entering by the exact returned id (instead of clicking the first
 * same-name card) keeps each engine working on the workspace it created.
 */
export async function createWorkspaceViaApi(
  page: Page,
  name: string
): Promise<string> {
  const response = await page.request.post('/api/workspaces', {
    headers: CSRF_HEADERS,
    data: { name, workflow_mode: 'demo' },
  })
  expect(response.ok()).toBeTruthy()
  const { workspace } = (await response.json()) as { workspace: { id: string } }
  return workspace.id
}

/**
 * The demo workflow's start node accepts material items only
 * (EXEC-WORKFLOW-START-001). Smoke specs exercise the ref (粘贴 ID) path, so
 * they widen the entry contract by publishing a revision accepting both item
 * types before driving AddItemsDialog. API-driven to keep the UI smoke fast;
 * callers should reload the page afterwards so cached definitions refetch.
 */
export async function widenDemoWorkflowItemTypes(
  page: Page,
  workspaceId: string
): Promise<void> {
  const active = await page.request.get(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-revisions/active`
  )
  expect(active.ok()).toBeTruthy()
  const { definition_yaml: definitionYaml } = (await active.json()) as {
    definition_yaml: string
  }
  const definition = yaml.load(definitionYaml) as {
    nodes: Record<string, { type?: string; accepted_item_types?: string[] }>
  }
  const start = Object.values(definition.nodes).find(
    (node) => node.type === 'start'
  )
  expect(start).toBeTruthy()
  start!.accepted_item_types = ['material', 'ref']
  const publish = await page.request.post(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish`,
    {
      headers: CSRF_HEADERS,
      data: { definition_yaml: yaml.dump(definition) },
    }
  )
  expect(publish.ok()).toBeTruthy()
  const result = (await publish.json()) as { valid: boolean; errors: string[] }
  expect(result.errors).toEqual([])
}
