import { expect, test, type Locator, type Page } from '@playwright/test'

import type { RunBatchListItem } from '../src/api/runBatchesApi'
import { ensureAdminSession } from './helpers'

const WORKSPACE_ID = 'education_video_problems_generation'
const VIEWPORTS = [390, 700, 701, 1440]

test.use({ timezoneId: 'UTC' })

const pausedBatch: RunBatchListItem = {
  run_id: 'run-paused-layout',
  status: 'paused',
  items_total: 2400,
  processed_count: 1200,
  jobs_created: 1000,
  chunk_errors: [],
  error_message: '',
  created_at: '2026-10-21T02:21:00+00:00',
  updated_at: '2026-10-21T02:24:00+00:00',
  finished_at: null,
  created_by: 'e2e-layout',
  archived_at: null,
  archive_source: null,
  failed_item_count: 0,
  failed_item_count_lower_bound: false,
}

const endedBatch: RunBatchListItem = {
  ...pausedBatch,
  run_id: 'run-ended-layout',
  status: 'completed',
  processed_count: 2400,
  jobs_created: 2400,
  finished_at: '2026-10-21T02:23:00+00:00',
}

async function mockTaskCenterData(page: Page): Promise<void> {
  await page.route(
    `**/api/workspaces/${WORKSPACE_ID}/run-batches?*`,
    async (route) => {
      const view = new URL(route.request().url()).searchParams.get('view')
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          batches: view === 'active' ? [pausedBatch] : [endedBatch],
          active_count: 1,
          next_cursor: null,
        }),
      })
    }
  )
  await page.route(
    `**/api/workspaces/${WORKSPACE_ID}/job-batch-operations?*`,
    async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          operations: [],
          active_count: 0,
          next_cursor: null,
        }),
      })
    }
  )
}

async function box(locator: Locator) {
  const value = await locator.boundingBox()
  expect(value).not.toBeNull()
  return value!
}

async function expectSectionLayout(note: Locator, narrow: boolean) {
  const title = note.locator('..')
  await expect(title).toHaveCSS('flex-direction', narrow ? 'column' : 'row')
  const titleBox = await box(title)
  const noteBox = await box(note)
  if (narrow) {
    expect(noteBox.y).toBeGreaterThan(titleBox.y + 10)
    expect(titleBox.height).toBeGreaterThan(noteBox.height + 10)
  } else {
    expect(Math.abs(noteBox.y - titleBox.y)).toBeLessThanOrEqual(1)
  }
}

async function expectFooterLayout(
  footer: Locator,
  elapsed: Locator,
  action: Locator,
  narrow: boolean
) {
  await expect(footer).toHaveCSS('flex-wrap', narrow ? 'wrap' : 'nowrap')
  const elapsedBox = await box(elapsed)
  const actionBox = await box(action)
  if (narrow) {
    expect(actionBox.y).toBeGreaterThanOrEqual(
      elapsedBox.y + elapsedBox.height - 0.5
    )
  } else {
    expect(actionBox.y).toBeLessThan(elapsedBox.y + elapsedBox.height)
    expect(actionBox.y + actionBox.height).toBeGreaterThan(elapsedBox.y)
    expect(elapsedBox.x + elapsedBox.width).toBeLessThanOrEqual(actionBox.x)
  }
}

async function expectNoHorizontalOverflow(page: Page, width: number) {
  if (width === 390) {
    await page.evaluate(() => {
      const shell = document.querySelector('.app-shell > *')
      shell?.getAnimations().forEach((animation) => {
        animation.pause()
        animation.currentTime = 0
      })
    })
  }
  const main = page.locator('main').last()
  const dimensions = await main.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }))
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth)
  const diagnostics = await page.evaluate(() => {
    const clientWidth = document.documentElement.clientWidth
    const describe = (element: Element) => {
      const rect = element.getBoundingClientRect()
      return {
        element: `${element.tagName.toLowerCase()}.${element.className}`,
        left: rect.left,
        right: rect.right,
        width: rect.width,
        transform: getComputedStyle(element).transform,
      }
    }
    return {
      clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      shell: describe(document.querySelector('.app-shell > *')!),
      offenders: [...document.querySelectorAll('body *')]
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          return rect.left < 0 || rect.right > clientWidth
        })
        .map(describe),
    }
  })
  expect(
    diagnostics.scrollWidth,
    JSON.stringify(diagnostics)
  ).toBeLessThanOrEqual(diagnostics.clientWidth)
  for (const id of ['run-paused-layout', 'run-ended-layout']) {
    const cardBox = await box(page.getByTestId(`task-card-${id}`))
    expect(cardBox.x).toBeGreaterThanOrEqual(0)
    expect(cardBox.x + cardBox.width).toBeLessThanOrEqual(width)
  }
  if (width === 390) {
    await page.evaluate(() => {
      const shell = document.querySelector('.app-shell > *')
      shell?.getAnimations().forEach((animation) => animation.play())
    })
  }
}

test('任务中心在 390/700/701/1440px 保持标题、卡片底行与页面宽度布局', async ({
  page,
}) => {
  await ensureAdminSession(page)
  await mockTaskCenterData(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/workspaces/${WORKSPACE_ID}/tasks`)

  const pausedCard = page.getByTestId('task-card-run-paused-layout')
  const endedCard = page.getByTestId('task-card-run-ended-layout')
  await expect(pausedCard).toBeVisible()
  await expect(endedCard).toBeVisible()

  for (const width of VIEWPORTS) {
    await test.step(`${width}px`, async () => {
      await page.setViewportSize({ width, height: 1000 })
      const narrow = width <= 700
      await expectSectionLayout(
        page.getByText('· 进行中按发起时间倒序，排队按提交顺序', {
          exact: true,
        }),
        narrow
      )
      await expectSectionLayout(
        page.getByText('· 按结束时间倒序 · 首屏只加载最近几条，点击加载更早', {
          exact: true,
        }),
        narrow
      )
      const pausedElapsed = pausedCard.getByText(
        '已暂停 · 后台处理已挂起，已处理部分保留',
        { exact: true }
      )
      await expectFooterLayout(
        pausedElapsed.locator('..'),
        pausedElapsed,
        pausedCard.getByRole('button', { name: '继续', exact: true }),
        narrow
      )
      const endedElapsed = endedCard.getByText('耗时 2 分钟 · 10月21日 完成', {
        exact: true,
      })
      await expectFooterLayout(
        endedElapsed.locator('..'),
        endedElapsed,
        endedCard.getByRole('button', { name: '查看任务', exact: true }),
        narrow
      )
      await expectNoHorizontalOverflow(page, width)
    })
  }
})
