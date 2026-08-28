import { expect, test } from '@playwright/test'

import { ensureAdminSession, widenDemoWorkflowItemTypes } from './helpers'


test('创建 workspace、批量建 job 并查看 job 节点', async ({ page }, testInfo) => {
  // Unique per engine+attempt: the smoke suite shares one database across
  // browser engines (chromium runs first, then firefox/webkit), and the
  // workspace list renders oldest-first — a reused name would send the
  // run-creation step into the previous engine's workspace, where the
  // (source_type, source_id) dedup rejects the same Q1 item with
  // "No tasks were resolved from input".
  const WORKSPACE_NAME = `E2E 冒烟工作区 ${testInfo.project.name}-${testInfo.retry}`
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

  // exact：name 默认子串匹配，会同时命中空 workspace 引导卡片里的
  // disabled「添加条目」按钮（strict mode violation，视挂载时序 flaky）。
  await page.getByRole('button', { name: '添加', exact: true }).click()
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
  // Scope to the detail page's progress panel: the workspace job list also
  // renders each job's current node label (its activeLabel), and
  // smoke-job-rerun.spec.ts has already created a Q1 job in the same shared
  // demo workspace — a page-wide getByText would hit both job rows'
  // activeLabels and fail with a strict mode violation (two jobs, both
  // showing 读取知识点 while pending). The panel root carries the 查看 DAG
  // button, so its ancestor chain anchors the timeline below it.
  const progressPanel = page
    .getByRole('button', { name: '查看 DAG' })
    .locator('xpath=ancestor::div[contains(@class, "panel")][1]')
  await expect(progressPanel.getByText('读取知识点')).toBeVisible()
  await expect(progressPanel.getByText('生成练习题')).toBeVisible()
})
