import { expect, test } from '@playwright/test'

import { ensureAdminSession, widenDemoWorkflowItemTypes } from './helpers'

// The demo workspace is pre-seeded by scripts/e2e/run_browser_smoke.py
// (schema v61: creation no longer seeds the sample template).
const DEMO_WORKSPACE_ID = 'education_video_problems_generation'


test('在 job 详情页通过重跑对话框重跑节点', async ({ page }, testInfo) => {
  await ensureAdminSession(page)

  // Unique per engine+attempt: retries rerun the whole spec, and the
  // (connection, external_id) dedup would reject a replayed create-run.
  const externalId = `Q1-${testInfo.project.name}-${testInfo.retry}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')

  // The whole flow drives the pre-seeded demo workspace; each engine reruns
  // the spec against the same workspace, and the (source_type, source_id)
  // dedup is bypassed by the unique per-run job-batch semantics of the
  // widened demo workflow.
  await page.goto(`/workspaces/${DEMO_WORKSPACE_ID}`)
  await expect(page).toHaveURL(new RegExp(`/workspaces/${DEMO_WORKSPACE_ID}$`))

  // The demo workflow is material-only (start node accepted_item_types); the
  // ref path below needs the contract widened first (EXEC-WORKFLOW-START-001).
  await widenDemoWorkflowItemTypes(page, DEMO_WORKSPACE_ID)
  await page.reload()

  // exact：name 默认子串匹配，会同时命中空 workspace 引导卡片里的
  // disabled「添加条目」按钮（strict mode violation，视挂载时序 flaky）。
  await page.getByRole('button', { name: '添加', exact: true }).click()
  // 添加 opens AddItemsDialog; the legacy intake entry is retired (#154), so
  // the smoke path uses the 粘贴 ID tab with the seeded `cms-internal`
  // external connection (ref items only require the connection to exist).
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('tab', { name: '粘贴 ID' }).click()
  await addItemsDialog.getByLabel('连接 Key').fill('cms-internal')
  await addItemsDialog.getByLabel('外部 ID').fill(externalId)
  await addItemsDialog.getByRole('button', { name: '创建运行' }).click()
  // A successful submit closes the dialog; wait for it (the modal overlay
  // intercepts pointer events while it is in the DOM, so clicking the job
  // row before the close lands — a real risk on slow CI runners — fails).
  await expect(addItemsDialog).toBeHidden({ timeout: 15_000 })

  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)

  // The rerun action is injected into the app bar once the detail loads.
  const rerunButton = page.getByRole('button', { name: '重跑', exact: true })
  await expect(rerunButton).toBeEnabled()
  await rerunButton.click()

  const rerunDialog = page.getByRole('dialog')
  await expect(
    rerunDialog.getByRole('heading', { name: '选择重跑节点' })
  ).toBeVisible()
  await rerunDialog.getByRole('button', { name: '评审练习题' }).click()
  await rerunDialog.getByRole('button', { name: '确认重跑' }).click()

  // No worker runs in this environment, so nodes stay pending; the rerun
  // marks downstream nodes stale, which the progress panel shows as 已过期.
  await expect(rerunDialog).toBeHidden()
  await expect(page.getByText('已过期').first()).toBeVisible()
})
