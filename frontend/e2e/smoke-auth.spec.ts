import { expect, test } from '@playwright/test'

import { ADMIN } from './helpers'

// Fresh-database flow: the runner recreates the E2E database per run, and
// this spec sorts before the others, so bootstrap is still available here.
test('bootstrap 首个管理员后可登出并重新登录', async ({ page }) => {
  await page.goto('/setup')
  await expect(
    page.getByRole('heading', { name: '初始化管理员' })
  ).toBeVisible()

  await page.getByLabel('用户名').fill(ADMIN.username)
  await page.getByLabel('显示名（可选）').fill(ADMIN.displayName)
  await page.getByLabel('密码', { exact: true }).fill(ADMIN.password)
  await page.getByLabel('确认密码').fill(ADMIN.password)
  await page.getByRole('button', { name: '创建并登录' }).click()

  await expect(page).toHaveURL('/')
  await expect(
    page.getByRole('heading', { name: 'Agent Legion' })
  ).toBeVisible()

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL('/login')
  await expect(
    page.getByRole('heading', { name: '登录 Agent Legion' })
  ).toBeVisible()

  await page.getByLabel('用户名').fill(ADMIN.username)
  await page.getByLabel('密码', { exact: true }).fill(ADMIN.password)
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page).toHaveURL('/')
  await expect(
    page.getByRole('button', { name: '新建 Workspace' })
  ).toBeVisible()
})
