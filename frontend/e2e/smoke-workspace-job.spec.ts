import { expect, test } from '@playwright/test'

import { ensureAdminSession, widenDemoWorkflowItemTypes } from './helpers'

// The demo workspace is pre-seeded by scripts/e2e/run_browser_smoke.py
// (schema v61: creation no longer seeds the sample template).
const DEMO_WORKSPACE_ID = 'education_video_problems_generation'


test('创建 workspace、批量建 job 并查看 job 节点', async ({ page }, testInfo) => {
  // Unique per engine+attempt: the smoke suite shares one database across
  // browser engines, and the creation flow asserts the fresh workspace's
  // onboarding guide — a reused id would 409.
  const WORKSPACE_ID = `e2e_ws_${testInfo.project.name}_${testInfo.retry}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')
  const WORKSPACE_NAME = `E2E 冒烟工作区 ${testInfo.project.name}-${testInfo.retry}`
  await ensureAdminSession(page)

  await page.goto('/')
  const createButton = page.getByRole('button', { name: '新建 Workspace' })
  await expect(createButton).toBeVisible()
  await createButton.click()

  // Schema v61: explicit id (bound to the workflow key at creation) + name.
  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('Workspace ID').fill(WORKSPACE_ID)
  await createDialog.getByLabel('Workspace 名称').fill(WORKSPACE_NAME)
  await createDialog.getByRole('button', { name: '创建' }).click()
  await expect(createDialog).toBeHidden({ timeout: 30_000 })

  // The fresh workspace shows the 3-step onboarding guide (the original bug:
  // blank-canvas workspaces used to get a 400 from /stats and never showed it).
  await page.goto(`/workspaces/${WORKSPACE_ID}`)
  await expect(page.getByText('创建并发布 Workflow')).toBeVisible()

  // The job flow drives the pre-seeded demo workspace instead.
  await page.goto(`/workspaces/${DEMO_WORKSPACE_ID}`)
  await expect(page).toHaveURL(new RegExp(`/workspaces/${DEMO_WORKSPACE_ID}$`))

  // The demo workflow is material-only (start node accepted_item_types); the
  // ref path below needs the contract widened first (EXEC-WORKFLOW-START-001).
  await widenDemoWorkflowItemTypes(page, DEMO_WORKSPACE_ID)
  await page.reload()

  await page.getByRole('button', { name: '添加' }).click()
  // 添加 opens AddItemsDialog; the legacy intake entry is retired (#154), so
  // the smoke path uses the 粘贴 ID tab with the seeded `cms-internal`
  // external connection (ref items only require the connection to exist).
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('tab', { name: '粘贴 ID' }).click()
  await addItemsDialog.getByLabel('连接 Key').fill('cms-internal')
  await addItemsDialog.getByLabel('外部 ID').fill('Q1')
  await addItemsDialog.getByRole('button', { name: '创建运行' }).click()
  // A successful submit closes the dialog; wait for it (the modal overlay
  // intercepts pointer events while it is in the DOM, so clicking the job
  // row before the close lands — a real risk on slow CI runners — fails).
  await expect(addItemsDialog).toBeHidden({ timeout: 15_000 })

  // The runs API creates jobs synchronously; no workflow worker is started,
  // so the job appears with all nodes still pending.
  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()

  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)
  await expect(page.getByText('读取知识点')).toBeVisible()
  await expect(page.getByText('生成练习题')).toBeVisible()
})
