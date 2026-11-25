import { expect, test, type Page } from '@playwright/test'
import { existsSync } from 'node:fs'
import type { components } from '../src/generated/api'
import { ensureAdminSession } from './helpers'

const SOURCE_WORKSPACE = 'education_video_problems_generation'
const CSRF_HEADERS = { 'x-agent-legion-request': '1' }
type ResourceList = components['schemas']['SharedResourceListResponse']
type RunResponse = components['schemas']['RunCreateResponse']
type RunAccepted = components['schemas']['RunAcceptedResponse']
type RunDetail = components['schemas']['RunDetailResponse']
type MaterialList = components['schemas']['MaterialListResponse']
type JobDetail = components['schemas']['JobDetailResponse']

test.use({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  locale: 'zh-CN',
})

async function capture(
  page: Page,
  filename: string,
  selector?: string
): Promise<void> {
  const output = process.env.UI_ACCEPTANCE_DIR
  if (!output) return
  const path = `${output}/1553-shared-resources/${filename}.png`
  if (process.env.UI_ACCEPTANCE_ONLY_NEW === '1' && existsSync(path)) return
  if (selector) {
    await page.locator(selector).screenshot({ path, animations: 'disabled' })
    return
  }
  await page.screenshot({ path, fullPage: true, animations: 'disabled' })
}

async function createRunFromFirstMaterial(
  page: Page,
  resourceName: string,
  version: number,
  options?: { delayResourcesMs?: number }
): Promise<RunResponse> {
  const delay = options?.delayResourcesMs ?? 0
  if (delay > 0) {
    await page.route(
      `**/${SOURCE_WORKSPACE}/shared-resources?*`,
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, delay))
        await route.continue()
      }
    )
  }
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}`)
  await page.getByRole('button', { name: '添加', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '添加条目' })
  await dialog.getByRole('tab', { name: '已有材料' }).click()
  await dialog.getByRole('checkbox').first().check()
  const createButton = dialog.getByRole('button', { name: '创建运行' })
  if (delay > 0) {
    await expect(createButton).toBeDisabled()
    await expect(dialog.getByText('正在重新确认共享资源…')).toBeVisible()
    await expect(
      dialog.getByText(`${resourceName} v1`, { exact: true })
    ).toHaveCount(0)
    await capture(page, '05-run-confirmation-refetch-disabled-desktop')
  }
  await expect(
    dialog.getByText(`${resourceName} v${version}`, { exact: true })
  ).toBeVisible()
  await expect(createButton).toBeEnabled()
  if (delay > 0) {
    await page.unroute(`**/${SOURCE_WORKSPACE}/shared-resources?*`)
  }
  await capture(page, `05-run-confirmation-v${version}-desktop`)
  const [response] = await Promise.all([
    page.waitForResponse(
      (item) =>
        item.url().includes(`/api/workspaces/${SOURCE_WORKSPACE}/runs`) &&
        item.request().method() === 'POST'
    ),
    createButton.click(),
  ])
  expect(response.ok()).toBeTruthy()
  await expect(dialog).toBeHidden({ timeout: 15_000 })
  return (await response.json()) as RunResponse
}

test('共享资源三任务接入真实平台，并覆盖版本历史、run 读取、移动端与 viewer', async ({
  page,
  browser,
}, testInfo) => {
  test.setTimeout(300_000)
  const suffix = `${testInfo.project.name}-${testInfo.retry}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')
  const resourceName = `E2E 审核规则 ${suffix}`
  const targetId = `e2e_resource_target_${suffix}`
  const targetName = `E2E 共享目标 ${suffix}`
  await ensureAdminSession(page)

  const targetResponse = await page.request.post('/api/workspaces', {
    headers: CSRF_HEADERS,
    data: { id: targetId, name: targetName },
  })
  expect(targetResponse.ok()).toBeTruthy()

  // A/B 的公共起点：通过真实资源库从现有材料创建资源。
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/resources`)
  await page.getByRole('button', { name: '从材料或文件夹创建' }).click()
  const createDialog = page.getByRole('dialog', { name: '创建共享资源' })
  await createDialog.getByLabel('资源名称').fill(resourceName)
  await createDialog
    .getByLabel('来源', { exact: true })
    .selectOption({ index: 1 })
  await createDialog.getByRole('button', { name: '创建资源' }).click()
  await expect(createDialog).toBeHidden({ timeout: 30_000 })
  const resourceCard = page.locator('article').filter({ hasText: resourceName })
  await expect(resourceCard).toBeVisible()
  await expect(
    resourceCard.getByRole('button', { name: '添加到工作流' })
  ).toBeVisible()
  await expect(resourceCard.getByText(/v\d/)).toHaveCount(0)
  await expect(resourceCard.getByText(/hash|sha256/i)).toHaveCount(0)
  await capture(page, '01-resource-library-desktop')

  const resourcesResponse = await page.request.get(
    `/api/workspaces/${SOURCE_WORKSPACE}/shared-resources?limit=200`
  )
  const resources = (await resourcesResponse.json()) as ResourceList
  const resource = resources.resources.find(
    (item) => item.name === resourceName
  )
  expect(resource).toBeTruthy()
  const resourceId = resource!.id

  // B：首次授权、重复回执、切换到目标后看到同一资源。
  await resourceCard.getByRole('button', { name: '共享' }).click()
  const shareDialog = page.getByRole('dialog', {
    name: `共享「${resourceName}」`,
  })
  await shareDialog.getByLabel('目标工作区').selectOption(targetId)
  await shareDialog.getByRole('button', { name: `共享到${targetName}` }).click()
  await expect(shareDialog.getByText(`已共享到${targetName}。`)).toBeVisible()
  await shareDialog.getByRole('button', { name: `共享到${targetName}` }).click()
  await expect(
    shareDialog.getByText(`此前已共享到${targetName}，无需重复操作。`)
  ).toBeVisible()
  await capture(page, '02-share-duplicate-desktop')
  await shareDialog.getByRole('button', { name: `切换到${targetName}` }).click()
  await expect(page).toHaveURL(new RegExp(`/workspaces/${targetId}/resources$`))
  await expect(
    page.locator('article').filter({ hasText: resourceName })
  ).toBeVisible()
  await expect(page.getByText('正在加载资源库…')).toHaveCount(0)
  await capture(page, '03-shared-target-desktop-verified')

  // A：资源库 → Studio 节点选择 → 原有草稿发布 → 创建运行确认 v1。
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/resources`)
  await page
    .locator('article')
    .filter({ hasText: resourceName })
    .getByRole('button', { name: '添加到工作流' })
    .click()
  const studioDialog = page.getByRole('dialog', { name: '添加到工作流' })
  await expect(studioDialog.getByLabel('使用资源的节点')).toBeVisible()
  const addButton = studioDialog.getByRole('button', { name: '添加到工作流' })
  await expect(addButton).toBeEnabled()
  await capture(page, '04-studio-resource-dialog-desktop')
  await addButton.click()
  await expect(studioDialog).toBeHidden()
  let draftYaml = ''
  await expect(async () => {
    const response = await page.request.get(
      `/api/workspaces/${SOURCE_WORKSPACE}/workflow-draft`
    )
    expect(response.ok()).toBeTruthy()
    const draft = (await response.json()) as { definition_yaml?: string | null }
    expect(draft.definition_yaml).toContain(resourceId)
    draftYaml = draft.definition_yaml!
  }).toPass({ timeout: 20_000 })
  const publish = await page.request.post(
    `/api/workspaces/${SOURCE_WORKSPACE}/workflow-drafts/publish`,
    { headers: CSRF_HEADERS, data: { definition_yaml: draftYaml } }
  )
  expect(publish.ok()).toBeTruthy()
  expect(((await publish.json()) as { valid: boolean }).valid).toBe(true)

  const firstRun = await createRunFromFirstMaterial(page, resourceName, 1)
  expect(firstRun.job_ids[0]).toBeTruthy()
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/jobs/${firstRun.job_ids[0]}`)
  await expect(
    page.getByText(`${resourceName} v1`, { exact: true })
  ).toBeVisible()
  await capture(page, '06-job-v1-desktop', 'section[aria-label="本次使用版本"]')

  const resume = await page.request.post(
    `/api/worker/resume?workspace_id=${SOURCE_WORKSPACE}`,
    { headers: CSRF_HEADERS }
  )
  expect(resume.ok()).toBeTruthy()
  await expect(async () => {
    const response = await page.request.get(`/api/jobs/${firstRun.job_ids[0]}`)
    expect(response.ok()).toBeTruthy()
    const detail = (await response.json()) as JobDetail
    expect(
      detail.nodes.find((node) => node.node_key === 'intake_knowledge_points')
        ?.status
    ).toBe('completed')
  }).toPass({ timeout: 60_000, intervals: [500, 1_000, 2_000] })
  const pause = await page.request.post(
    `/api/worker/pause?workspace_id=${SOURCE_WORKSPACE}`,
    { headers: CSRF_HEADERS }
  )
  expect(pause.ok()).toBeTruthy()

  // C：主动创建并升级 v2，再创建第二个任务；管理页比较真实新旧任务。
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/resources/${resourceId}`)
  await page.getByRole('button', { name: '创建资源版本' }).click()
  const versionDialog = page.getByRole('dialog', { name: '创建资源版本' })
  await versionDialog
    .getByLabel('来源', { exact: true })
    .selectOption({ index: 2 })
  await versionDialog.getByRole('button', { name: '创建新版本' }).click()
  await expect(versionDialog).toBeHidden({ timeout: 30_000 })
  await page.getByRole('button', { name: '升级到 v2（仅新任务使用）' }).click()
  await expect(
    page.getByText('之后创建的任务将使用 v2，已有任务不变。')
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: '文件差异' })).toBeVisible()

  const secondRun = await createRunFromFirstMaterial(page, resourceName, 2, {
    delayResourcesMs: 1500,
  })
  expect(secondRun.job_ids[0]).toBeTruthy()
  expect(secondRun.job_ids[0]).not.toBe(firstRun.job_ids[0])
  await page.goto(
    `/workspaces/${SOURCE_WORKSPACE}/jobs/${secondRun.job_ids[0]}`
  )
  await expect(
    page.getByText(`${resourceName} v2`, { exact: true })
  ).toBeVisible()
  await capture(page, '06-job-v2-desktop', 'section[aria-label="本次使用版本"]')

  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/resources/${resourceId}`)
  await expect(page.getByText(`${resourceName} · 本次使用 v1`)).toBeVisible()
  await expect(page.getByText(`${resourceName} · 本次使用 v2`)).toBeVisible()
  await expect(page.getByText('当前已改为 v2，本任务不变')).toBeVisible()
  await capture(page, '07-version-history-desktop')
  await capture(
    page,
    '07-job-version-comparison-desktop',
    'section[aria-labelledby="resource-jobs-title"]'
  )

  // run 详情：一条真实异步受理记录也必须在展开后返回同一冻结版本。
  const materialsResponse = await page.request.get(
    `/api/workspaces/${SOURCE_WORKSPACE}/materials?limit=200`
  )
  const materials = (await materialsResponse.json()) as MaterialList
  const asyncResponse = await page.request.post(
    `/api/workspaces/${SOURCE_WORKSPACE}/runs`,
    {
      headers: CSRF_HEADERS,
      data: {
        mode: 'async',
        items: [{ type: 'material', material_id: materials.materials[0].id }],
      },
    }
  )
  expect(asyncResponse.status()).toBe(202)
  const accepted = (await asyncResponse.json()) as RunAccepted
  const runId = accepted.run.id
  await expect(async () => {
    const response = await page.request.get(
      `/api/workspaces/${SOURCE_WORKSPACE}/runs/${runId}`
    )
    expect(response.ok()).toBeTruthy()
    const detail = (await response.json()) as RunDetail
    expect(detail.run.shared_resources?.[0]?.version).toBe(2)
  }).toPass({ timeout: 30_000 })
  await page.goto(`/workspaces/${SOURCE_WORKSPACE}/tasks`)
  const runCard = page.getByTestId(`task-card-${runId}`)
  await runCard.getByText('本次使用版本').click()
  await expect(
    runCard.getByText(`${resourceName} v2`, { exact: true })
  ).toBeVisible()
  await capture(page, '08-run-detail-desktop')

  // 移动端与 viewer：同一生产页面，只读写操作不可用且无横向溢出。
  const username = `e2e-viewer-${suffix}`
  const password = 'e2e-viewer-password-1'
  const userResponse = await page.request.post('/api/users', {
    headers: CSRF_HEADERS,
    data: { username, password, display_name: 'E2E Viewer', role: 'member' },
  })
  expect(userResponse.ok()).toBeTruthy()
  const user = (await userResponse.json()) as { id: string }
  const memberResponse = await page.request.put(
    `/api/workspaces/${SOURCE_WORKSPACE}/members`,
    { headers: CSRF_HEADERS, data: { user_id: user.id, role: 'viewer' } }
  )
  expect(memberResponse.ok()).toBeTruthy()
  const targetMemberResponse = await page.request.put(
    `/api/workspaces/${targetId}/members`,
    { headers: CSRF_HEADERS, data: { user_id: user.id, role: 'editor' } }
  )
  expect(targetMemberResponse.ok()).toBeTruthy()
  const viewerContext = await browser.newContext({
    baseURL: new URL(page.url()).origin,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    locale: 'zh-CN',
  })
  const viewerPage = await viewerContext.newPage()
  await viewerPage.setViewportSize({ width: 390, height: 844 })
  const login = await viewerPage.request.post('/api/auth/login', {
    headers: CSRF_HEADERS,
    data: { username, password },
  })
  expect(login.ok()).toBeTruthy()
  await viewerPage.goto(`/workspaces/${SOURCE_WORKSPACE}/resources`)
  const viewerCard = viewerPage
    .locator('article')
    .filter({ hasText: resourceName })
  await expect(viewerCard).toBeVisible()
  await expect(
    viewerCard.getByRole('button', { name: '添加到工作流' })
  ).toBeDisabled()
  await expect(
    viewerPage.getByText(/你在当前工作区为只读成员，可以查看和共享资源/)
  ).toBeVisible()
  await expect(
    viewerPage.getByText('无法确认编辑权限，已按只读方式显示。')
  ).toHaveCount(0)
  await capture(viewerPage, '09-viewer-library-mobile')
  await viewerCard.getByRole('button', { name: '共享' }).click()
  await expect(
    viewerPage.getByText(/可以共享到你有编辑权限的其他工作区，不能撤销已有共享/)
  ).toBeVisible()
  const viewerShareDialog = viewerPage.getByRole('dialog', {
    name: `共享「${resourceName}」`,
  })
  await viewerShareDialog.getByLabel('目标工作区').selectOption(targetId)
  await viewerShareDialog
    .getByRole('button', { name: `共享到${targetName}` })
    .click()
  await expect(
    viewerShareDialog.getByText(`此前已共享到${targetName}，无需重复操作。`)
  ).toBeVisible()
  await expect(
    viewerShareDialog.getByRole('button', { name: '停止新任务使用' })
  ).toHaveCount(0)
  await viewerShareDialog.getByRole('button', { name: '关闭' }).click()
  await expect(
    viewerPage.getByRole('dialog', { name: `共享「${resourceName}」` })
  ).toBeHidden()
  await viewerPage.goto(
    `/workspaces/${SOURCE_WORKSPACE}/resources/${resourceId}`
  )
  await expect(
    viewerPage.getByRole('button', { name: '切回到 v1（仅新任务使用）' })
  ).toBeDisabled()
  await expect(
    viewerPage.getByRole('button', { name: '创建资源版本' })
  ).toHaveCount(0)
  await expect(
    viewerPage.getByText('你在当前工作区为只读成员，只能查看版本和任务记录。')
  ).toBeVisible()
  expect(
    await viewerPage.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true)
  await capture(viewerPage, '10-viewer-manage-mobile')
  await viewerContext.close()
})
