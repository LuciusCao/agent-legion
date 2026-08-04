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
  await createDialog.getByRole('combobox', { name: '工作流' }).click()
  await page.getByRole('option', { name: '题目审题信息生成 DAG' }).click()
  await createDialog.getByRole('button', { name: '创建' }).click()
  await expect(createDialog).toBeHidden()

  await page.getByText(WORKSPACE_NAME).first().click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+$/)

  await page.getByRole('button', { name: '添加' }).click()
  const addDialog = page.getByRole('dialog')
  await addDialog.getByRole('combobox', { name: '导入模式' }).click()
  await page.getByRole('option', { name: '按题目ID批量' }).click()
  await addDialog.getByRole('textbox', { name: '按题目ID批量' }).fill('Q1')
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
  await rerunDialog.getByTestId('rerun-chip-review_key_info').click()
  await rerunDialog.getByRole('button', { name: '确认重跑' }).click()

  // No worker runs in this environment, so nodes stay pending; the rerun
  // marks downstream nodes stale, which the progress panel shows as 已过期.
  await expect(rerunDialog).toBeHidden()
  await expect(page.getByText('已过期').first()).toBeVisible()
})
