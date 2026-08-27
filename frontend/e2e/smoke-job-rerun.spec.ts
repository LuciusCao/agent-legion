import { expect, test } from '@playwright/test'

import { ensureAdminSession, widenDemoWorkflowItemTypes } from './helpers'


test('在 job 详情页通过重跑对话框重跑节点', async ({ page }, testInfo) => {
  // Unique per engine+attempt: the smoke suite shares one database across
  // browser engines (chromium runs first, then firefox/webkit), and the
  // workspace list renders oldest-first — a reused name would send the
  // run-creation step into the previous engine's workspace, where the
  // (source_type, source_id) dedup rejects the same Q1 item with
  // "No tasks were resolved from input".
  const WORKSPACE_NAME = `E2E 重跑工作区 ${testInfo.project.name}-${testInfo.retry}`
  await ensureAdminSession(page)

  await page.goto('/')
  const createButton = page.getByRole('button', { name: '新建 Workspace' })
  await expect(createButton).toBeVisible()
  await createButton.click()

  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('Workspace 名称').fill(WORKSPACE_NAME)
  await createDialog
    .getByRole('checkbox', {
      name: '从示例模板初始化（教学视频脚本与题目生成）',
    })
    .check()
  await createDialog.getByRole('button', { name: '创建' }).click()
  // Demo-template workspaces seed node code server-side; the POST can take
  // seconds (4-5s observed locally), so give the close a generous timeout.
  await expect(createDialog).toBeHidden({ timeout: 30_000 })

  await page.getByText(WORKSPACE_NAME).first().click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+$/)

  // The demo workflow is material-only (start node accepted_item_types); the
  // ref path below needs the contract widened first (EXEC-WORKFLOW-START-001).
  const workspaceId = page.url().split('/workspaces/')[1]
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
