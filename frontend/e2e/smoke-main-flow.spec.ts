import { expect, test } from '@playwright/test'

import { ensureAdminSession } from './helpers'

// Seeded by scripts/e2e/run_browser_smoke.py (scripts/e2e/_main_flow_seed.py):
// a hybrid DAG (_start(ref) → intake(code) → draft(velites Agent, stub LLM
// gateway) → publish(code)) with dispatch resumed, plus a standalone Worker
// process claiming the Agent node. The demo workspace stays paused.
const WORKSPACE_ID = 'e2e_main_flow'

// JobDetail.job.status / nodes[].status come from GET /api/jobs/{jobId}.
interface JobDetailPayload {
  job: { status: string }
  nodes: { node_key: string; label: string; status: string }[]
}

test('主流程：添加条目 → 节点真实执行 → job 完成 → 产物断言 → 打包下载', async ({
  page,
}, testInfo) => {
  // Real node execution (2 code nodes + 1 velites Agent via stub gateway)
  // takes tens of seconds even warm; the default 30s test timeout is far
  // below the completion poll below.
  test.setTimeout(300_000)
  // Unique per engine+attempt: the suite shares one database across browser
  // engines, retries rerun the whole spec, and ref items dedup on
  // (connection, external_id).
  const externalId = `MF-${testInfo.project.name}-${testInfo.retry}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')
  await ensureAdminSession(page)

  await page.goto(`/workspaces/${WORKSPACE_ID}`)
  await expect(page).toHaveURL(new RegExp(`/workspaces/${WORKSPACE_ID}$`))

  // 添加条目（粘贴 ID）：ref item + seeded cms-internal connection，创建运行
  // 同步建 job（与既有 smoke spec 同一条路径）。
  await page.getByRole('button', { name: '添加', exact: true }).click()
  const addItemsDialog = page.getByRole('dialog', { name: '添加条目' })
  await addItemsDialog.getByRole('tab', { name: '粘贴 ID' }).click()
  await addItemsDialog.getByLabel('连接 Key').fill('cms-internal')
  await addItemsDialog.getByLabel('外部 ID').fill(externalId)
  await addItemsDialog.getByRole('button', { name: '创建运行' }).click()
  // Wait for the close: the modal overlay intercepts pointer events while it
  // is in the DOM, so clicking the job row before it lands fails on slow CI.
  await expect(addItemsDialog).toBeHidden({ timeout: 15_000 })

  const jobRow = page.locator('[data-job]').first()
  await expect(jobRow).toBeVisible({ timeout: 30_000 })
  await jobRow.click()
  await expect(page).toHaveURL(/\/workspaces\/[^/]+\/jobs\/[^/]+$/)
  const jobId = page.url().split('/jobs/')[1]

  // 节点真实执行（Host code 池 ×2 + Worker 上 velites Agent ×1，LLM 走
  // stub gateway）：轮询 job 详情 API 直到 completed。失败时把节点状态带进
  // 断言信息，省一次 trace 复现。
  let detail: JobDetailPayload | undefined
  await expect(async () => {
    const response = await page.request.get(`/api/jobs/${encodeURIComponent(jobId)}`)
    expect(response.ok()).toBeTruthy()
    detail = (await response.json()) as JobDetailPayload
    expect(detail.job.status).toBe('completed')
  }).toPass({ timeout: 240_000, intervals: [1_000, 2_000, 5_000] })
  expect(
    detail!.nodes.map((node) => `${node.node_key}:${node.status}`).sort(),
    'every DAG node completed'
  ).toEqual(['draft:completed', 'intake:completed', 'publish:completed'])

  // 详情页浏览器级证据：进度面板渲染三个节点（含 Agent 节点）。
  await page.reload()
  const progressPanel = page
    .getByRole('button', { name: '查看 DAG' })
    .locator('xpath=ancestor::div[contains(@class, "panel")][1]')
  await expect(progressPanel.getByText('读取条目')).toBeVisible()
  await expect(progressPanel.getByText('生成草稿')).toBeVisible()
  await expect(progressPanel.getByText('汇总')).toBeVisible()

  // 产物：intake/publish 两个 code 节点 + stub Agent 写出的 draft.json。
  await page.getByRole('button', { name: '产物文件' }).click()
  const artifactDialog = page.getByRole('dialog')
  await expect(artifactDialog.getByText('intake_result.json')).toBeVisible()
  await expect(artifactDialog.getByText('draft.json')).toBeVisible()
  await expect(artifactDialog.getByText('publish_payload.json')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(artifactDialog).toBeHidden()

  // 打包下载：详情页「打包」→ jobs/package 返回 download_url → zip 可读。
  const packageButton = page.getByRole('button', { name: '打包', exact: true })
  await expect(packageButton).toBeEnabled()
  const [packageResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes(`/api/workspaces/${WORKSPACE_ID}/jobs/package`) &&
        response.request().method() === 'POST'
    ),
    packageButton.click(),
  ])
  expect(packageResponse.ok()).toBeTruthy()
  const { download_url: downloadUrl } = (await packageResponse.json()) as {
    download_url?: string
  }
  expect(downloadUrl).toBeTruthy()
  const zip = await page.request.get(downloadUrl!)
  expect(zip.ok()).toBeTruthy()
  expect(zip.headers()['content-type']).toContain('application/zip')
  expect((await zip.body()).byteLength).toBeGreaterThan(0)
})
