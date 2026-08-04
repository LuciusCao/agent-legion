import { expect, test } from '@playwright/test'

import { ensureAdminSession } from './helpers'

const WORKSPACE_NAME = 'E2E 冒烟工作区'

test('创建 workspace、批量建 job 并查看 job 节点', async ({ page }) => {
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

  // Intake runs in the server background queue; no workflow worker is
  // started, so the job appears with all nodes still pending.
  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()

  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)
  await expect(page.getByText('获取题目')).toBeVisible()
  await expect(page.getByText('清洗与解析')).toBeVisible()
})
