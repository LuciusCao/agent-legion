import { expect, test } from '@playwright/test'

import {
  createWorkspaceViaApi,
  ensureAdminSession,
  widenDemoWorkflowItemTypes,
} from './helpers'

const WORKSPACE_NAME = 'E2E 重跑工作区'

test('在 job 详情页通过重跑对话框重跑节点', async ({ page }) => {
  await ensureAdminSession(page)

  // Multi-browser runs share one database: the CJK name transliterates to
  // the same `e2e` slug, so engines after Chromium get suffixed ids and a
  // name-text click would enter the FIRST same-name workspace (an earlier
  // engine's, whose `cms-internal:Q1` job makes create_run's dedup drop the
  // item and 400). Create via API and enter by the exact returned id.
  const workspaceId = await createWorkspaceViaApi(page, WORKSPACE_NAME)
  await page.goto(`/workspaces/${workspaceId}`)
  await expect(page).toHaveURL(new RegExp(`/workspaces/${workspaceId}$`))

  // The demo workflow is material-only (start node accepted_item_types); the
  // ref path below needs the contract widened first (EXEC-WORKFLOW-START-001).
  await widenDemoWorkflowItemTypes(page, workspaceId)
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
