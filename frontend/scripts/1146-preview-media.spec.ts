/**
 * #1146 demo acceptance screenshots: job detail page with a custom preview
 * panel that plays a real ffmpeg-generated video via readArtifactBytes +
 * blob URL, with an SRT overlay driven by timeupdate.
 *
 * Run against the dedicated dev stack:
 *   E2E_BASE_URL=http://127.0.0.1:5186 npx playwright test --config playwright.1146.config.ts
 */
import { expect, test } from '@playwright/test'
import { ensureAdminSession } from '../e2e/helpers'

const FRONTEND = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5186'
const WORKSPACE_ID = 'demo_1146_media_preview'
const JOB_ID = 'demo_1146_media_preview_demo_1146_media_preview_demo-media-1'
const SHOT_DIR = '/Users/lucius/Desktop/0.7.19-ui/1146-preview-media'

test.use({ baseURL: FRONTEND })

test('预览面板视频播放 + 字幕叠加 + 整页上下文（#1146）', async ({ page }) => {
  await ensureAdminSession(page)

  await page.goto(`/workspaces/${WORKSPACE_ID}/jobs/${JOB_ID}`)
  await page.waitForLoadState('networkidle')

  const panel = page.frameLocator('[data-testid="preview-panel-host"] iframe')
  const video = panel.locator('#player')
  await expect(video).toBeVisible()

  // meta 行就绪 = readArtifactBytes 成功、blob URL 已建立。
  await expect(panel.locator('#meta')).toContainText('video/mp4', {
    timeout: 20_000,
  })
  await expect(panel.locator('#cap')).toContainText('字幕轨：3 条 cue', {
    timeout: 20_000,
  })

  // 视频真实渲染：readyState ≥ 2（有当前帧数据）且 seek 后有视频尺寸。
  await expect
    .poll(
      async () =>
        video.evaluate((el: HTMLVideoElement) => ({
          ready: el.readyState,
          w: el.videoWidth,
          h: el.videoHeight,
        })),
      { timeout: 20_000 }
    )
    .toMatchObject({ ready: expect.any(Number) })
  const dims = await video.evaluate((el: HTMLVideoElement) => ({
    ready: el.readyState,
    w: el.videoWidth,
    h: el.videoHeight,
  }))
  expect(dims.w).toBe(640)
  expect(dims.h).toBe(360)
  expect(dims.ready).toBeGreaterThanOrEqual(2)

  // 播放到字幕第 2 条区间（2.6s seek 由面板脚本完成，等字幕文本出现）。
  await expect(panel.locator('#subs')).toContainText('叠加字幕轨预览', {
    timeout: 15_000,
  })

  // 截图 1：面板内视频播放器 + 字幕叠加（核心交付）。
  const host = page.locator('[data-testid="preview-panel-host"]')
  await host.scrollIntoViewIfNeeded()
  await page.waitForTimeout(600)
  await host.screenshot({
    path: `${SHOT_DIR}/01-panel-video-subtitle-overlay.png`,
  })

  // 截图 2：job 详情整页上下文（左栏面板 + 右侧产物列表）。
  await page.waitForTimeout(400)
  await page.screenshot({
    path: `${SHOT_DIR}/02-job-detail-page-context.png`,
    fullPage: false,
  })

  console.log('screenshots written to', SHOT_DIR)
})
