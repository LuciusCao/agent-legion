import { expect, test } from '@playwright/test'

import { ensureAdminSession, widenDemoWorkflowItemTypes } from './helpers'

/**
 * #989：严格文档 CSP（script-src 'self' 'nonce-…'，无 'unsafe-inline'）下，
 * 生产构建与已发布预览面板照常可用。
 *
 * vite preview（E2E_BASE_URL）不下发 CSP，所以本 spec 直连 Host
 * （E2E_BACKEND_URL）：Host 托管同一份 frontend/dist 并对 index.html 附加
 * 带 per-response nonce 的文档策略。demo workspace 的已发布面板由
 * scripts/e2e/_preview_panel_seed.py 预置：其 <script> 必须执行（宿主盖
 * nonce），inline onclick= 必须被拦截并触发宿主提示。
 */
const BACKEND_URL = process.env.E2E_BACKEND_URL
const DEMO_WORKSPACE_ID = 'education_video_problems_generation'

test.skip(
  !BACKEND_URL,
  'needs the Host-served bundle: run via scripts/e2e/run_browser_smoke.py'
)
test.use({ baseURL: BACKEND_URL })

test('严格文档 CSP 下生产构建与已发布预览面板可用（#989）', async ({
  page,
}, testInfo) => {
  const externalId = `CSP-${testInfo.project.name}-${testInfo.retry}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')
  await ensureAdminSession(page)

  const response = await page.goto('/')
  const policy = response?.headers()['content-security-policy'] ?? ''
  const scriptSrc = policy
    .split(';')
    .map((directive) => directive.trim())
    .find((directive) => directive.startsWith('script-src '))
  expect(scriptSrc).toMatch(/^script-src 'self' 'nonce-[A-Za-z0-9_-]+'$/)
  // 生产 bundle 在严格策略下渲染（外链 module 脚本由 'self' 放行）。
  await expect(
    page.getByRole('button', { name: '新建 Workspace' })
  ).toBeVisible()

  await page.goto(`/workspaces/${DEMO_WORKSPACE_ID}`)
  await widenDemoWorkflowItemTypes(page, DEMO_WORKSPACE_ID)
  await page.reload()
  await page.getByRole('button', { name: '添加', exact: true }).click()
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('tab', { name: '粘贴 ID' }).click()
  await expect(
    addItemsDialog.getByRole('combobox', { name: /连接 Key/ })
  ).toHaveText('cms-internal')
  await addItemsDialog.getByLabel('外部 ID').fill(externalId)
  await addItemsDialog.getByRole('button', { name: '创建运行' }).click()
  await expect(addItemsDialog).toBeHidden({ timeout: 15_000 })
  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)

  const panel = page.frameLocator('[data-testid="preview-panel-host"] iframe')
  // 盖了 nonce 的 inline <script> 执行，桥 ready → init 往返成立。
  await expect(panel.locator('#status')).toHaveText('script-ran')
  await expect(panel.locator('#init')).toHaveText(/^init:/)
  await panel.locator('#listener').click()
  await expect(panel.locator('#status')).toHaveText('listener-ran')
  // inline 事件属性被拦截：状态不变，宿主显示拦截提示。
  await panel.locator('#inline-handler').click()
  await expect(page.getByText(/部分按钮或交互被安全策略拦截/)).toBeVisible()
  await expect(panel.locator('#status')).toHaveText('listener-ran')
})
