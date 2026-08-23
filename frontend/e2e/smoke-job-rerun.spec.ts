import { expect, test } from '@playwright/test'

import { ensureAdminSession } from './helpers'

const WORKSPACE_NAME = 'E2E 重跑工作区'

test('在 job 详情页通过重跑对话框重跑节点', async ({ page }) => {
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
  await expect(createDialog).toBeHidden()

  await page.getByText(WORKSPACE_NAME).first().click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+$/)

  await page.getByRole('button', { name: '添加' }).click()
  // 添加 now opens AddItemsDialog; the demo workflow still declares a legacy
  // intake mode, so 旧版接入模式 leads back to the old AddDialog flow.
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('button', { name: '旧版接入模式' }).click()
  const addDialog = page.getByRole('dialog', { name: '添加资源' })
  await addDialog.getByRole('textbox', { name: '按知识点批量' }).fill('Q1')
  await addDialog.getByRole('button', { name: '加入队列' }).click()

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
