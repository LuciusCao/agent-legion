/**
 * Playwright UI stress scenario for the workspace job list.
 *
 * This spec opens a workspace page that is receiving high-frequency patch
 * events, measures click/scroll responsiveness, records browser memory, and
 * counts long tasks. It is invoked by scripts/stress/run_e2e_stress.py.
 */

import { test, expect, Page } from '@playwright/test'
import fs from 'fs'
import path from 'path'

interface FrontendMetrics {
  durationSeconds: number
  clickLatenciesMs: number[]
  scrollLatenciesMs: number[]
  longTaskCount: number
  longTaskTotalDurationMs: number
  sseMessagesReceived: number
  sseMessagesPerSecond: number
  memorySamplesMb: number[]
  memoryHighWaterMb: number
  errors: string[]
}

const baseUrl = process.env.STRESS_BASE_URL || 'http://127.0.0.1:8000'
const workspaceId = process.env.STRESS_WORKSPACE || 'ws-stress'
const durationSeconds = parseInt(process.env.STRESS_DURATION || '300', 10)
const resultsDir = process.env.STRESS_RESULTS_DIR || path.join(process.cwd(), 'stress-results')

function percentile(values: number[], q: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const idx = Math.max(0, Math.min(sorted.length - 1, Math.floor(sorted.length * q)))
  return sorted[idx]
}

function summarizeMetrics(metrics: FrontendMetrics): Record<string, unknown> {
  const clicks = metrics.clickLatenciesMs
  const scrolls = metrics.scrollLatenciesMs
  return {
    ...metrics,
    clickLatencyP50Ms: percentile(clicks, 0.5),
    clickLatencyP95Ms: percentile(clicks, 0.95),
    scrollLatencyP50Ms: percentile(scrolls, 0.5),
    scrollLatencyP95Ms: percentile(scrolls, 0.95),
  }
}

async function measureClickLatency(page: Page): Promise<number> {
  const start = Date.now()
  await page.getByRole('button', { name: /refresh/i }).first().click({ force: true })
  // Wait for a short-lived DOM mutation to settle; this is intentionally coarse.
  await page.waitForTimeout(50)
  return Date.now() - start
}

async function measureScrollLatency(page: Page): Promise<number> {
  const start = Date.now()
  await page.mouse.wheel(0, 500)
  await page.waitForTimeout(50)
  return Date.now() - start
}

test('workspace page remains responsive under high job concurrency', async ({ page }) => {
  const metrics: FrontendMetrics = {
    durationSeconds,
    clickLatenciesMs: [],
    scrollLatenciesMs: [],
    longTaskCount: 0,
    longTaskTotalDurationMs: 0,
    sseMessagesReceived: 0,
    sseMessagesPerSecond: 0,
    memorySamplesMb: [],
    memoryHighWaterMb: 0,
    errors: [],
  }

  // Collect long tasks via PerformanceObserver when available.
  await page.evaluateOnNewDocument(() => {
    if (typeof window === 'undefined' || !('PerformanceObserver' in window)) return
    const longTasks: PerformanceEntry[] = []
    ;(window as unknown as Record<string, unknown>).__stressLongTasks = longTasks
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          longTasks.push(entry)
        }
      })
      observer.observe({ entryTypes: ['longtask'] })
    } catch {
      // PerformanceObserver longtask support is optional.
    }
  })

  // Listen to SSE traffic through the page.
  let sseMessages = 0
  page.on('response', async (response) => {
    const headers = await response.allHeaders()
    const contentType = headers['content-type'] || ''
    if (contentType.includes('text/event-stream')) {
      // We cannot easily stream the body in Playwright, so we approximate by
      // counting successful SSE connection responses. The backend stress script
      // records the actual message rate.
      sseMessages += 1
    }
  })

  const url = `${baseUrl}/workspaces/${workspaceId}`
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await expect(page).toHaveURL(/\/workspaces\//)

  const endTime = Date.now() + durationSeconds * 1000
  const sampleIntervalMs = 1000
  let lastSample = Date.now()

  while (Date.now() < endTime) {
    try {
      metrics.clickLatenciesMs.push(await measureClickLatency(page))
      metrics.scrollLatenciesMs.push(await measureScrollLatency(page))

      // Sample JS heap size when the API is available.
      const memory = await page.evaluate(() => {
        const perf = (window as unknown as Record<string, unknown>).performance
        const mem = (perf as Record<string, unknown>)?.memory as
          | { usedJSHeapSize?: number }
          | undefined
        return mem?.usedJSHeapSize ?? 0
      })
      const memoryMb = memory / (1024 * 1024)
      metrics.memorySamplesMb.push(memoryMb)
      metrics.memoryHighWaterMb = Math.max(metrics.memoryHighWaterMb, memoryMb)
    } catch (error) {
      metrics.errors.push(String(error))
    }

    const now = Date.now()
    if (now - lastSample >= sampleIntervalMs) {
      const longTasks = await page.evaluate(() => {
        return ((window as unknown as Record<string, unknown>).__stressLongTasks as
          | PerformanceEntry[]
          | undefined) || []
      })
      metrics.longTaskCount = longTasks.length
      metrics.longTaskTotalDurationMs = longTasks.reduce(
        (sum, entry) => sum + entry.duration,
        0
      )
      lastSample = now
    }

    await page.waitForTimeout(250)
  }

  metrics.sseMessagesReceived = sseMessages
  metrics.sseMessagesPerSecond = sseMessages / Math.max(1, durationSeconds)

  fs.mkdirSync(resultsDir, { recursive: true })
  const metricsPath = path.join(resultsDir, 'frontend-metrics.json')
  fs.writeFileSync(metricsPath, JSON.stringify(summarizeMetrics(metrics), null, 2))

  expect(metrics.errors).toHaveLength(0)
  expect(percentile(metrics.clickLatenciesMs, 0.95)).toBeLessThan(300)
})
