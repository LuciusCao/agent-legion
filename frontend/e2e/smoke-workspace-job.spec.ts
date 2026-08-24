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
  // 添加 opens AddItemsDialog; the legacy intake entry is retired (#154), so
  // the smoke path uses the 粘贴 ID tab with the seeded `cms-internal`
  // external connection (ref items only require the connection to exist).
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('tab', { name: '粘贴 ID' }).click()
  await addItemsDialog.getByLabel('连接 Key').fill('cms-internal')
  await addItemsDialog.getByLabel('外部 ID').fill('Q1')
  await addItemsDialog.getByRole('button', { name: '创建运行' }).click()

  // The runs API creates jobs synchronously; no workflow worker is started,
  // so the job appears with all nodes still pending.
  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()

  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)
  await expect(page.getByText('读取知识点')).toBeVisible()
  await expect(page.getByText('生成练习题')).toBeVisible()
})
