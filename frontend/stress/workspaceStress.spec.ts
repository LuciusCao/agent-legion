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
const resultsDir =
  process.env.STRESS_RESULTS_DIR || path.join(process.cwd(), 'stress-results')
const testTimeoutMs = durationSeconds * 2000 + 120_000

test.setTimeout(testTimeoutMs)

function percentile(values: number[], q: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const idx = Math.max(
    0,
    Math.min(sorted.length - 1, Math.floor(sorted.length * q))
  )
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

async function withProbeTimeout<T>(
  operation: Promise<T>,
  timeoutMs: number,
  label: string
): Promise<{ ok: true; value: T } | { ok: false; error: string }> {
  return Promise.race([
    operation
      .then((value) => ({ ok: true as const, value }))
      .catch((error) => ({ ok: false as const, error: String(error) })),
    new Promise<{ ok: false; error: string }>((resolve) => {
      setTimeout(() => {
        resolve({ ok: false, error: `${label} timed out after ${timeoutMs}ms` })
      }, timeoutMs)
    }),
  ])
}

async function measureClickLatency(page: Page): Promise<number> {
  const start = Date.now()
  await page.getByPlaceholder(/搜索 ID/).click({ force: true, timeout: 2000 })
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

test('workspace page remains responsive under high job concurrency', async ({
  page,
  context,
}) => {
  // Workspace APIs require an authenticated session; the stress runner logs in
  // server-side and hands the session cookie over via STRESS_SESSION_COOKIE.
  const sessionCookie = process.env.STRESS_SESSION_COOKIE
  if (sessionCookie) {
    await context.addCookies([
      { name: 'agent_legion_session', value: sessionCookie, url: baseUrl },
    ])
  }
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
  await page.addInitScript(() => {
    if (typeof window === 'undefined' || !('PerformanceObserver' in window))
      return
    const longTasks: PerformanceEntry[] = []
    ;(window as unknown as Record<string, unknown>).__stressLongTasks =
      longTasks
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

  // Count real SSE messages by monkey-patching EventSource before page scripts
  // run. This gives an actual message throughput number rather than connection
  // response count.
  await page.addInitScript(() => {
    if (typeof window === 'undefined' || !window.EventSource) return
    const OriginalEventSource = window.EventSource
    let messageCount = 0
    class StressEventSource extends OriginalEventSource {
      constructor(url: string | URL, eventSourceInitDict?: EventSourceInit) {
        super(url, eventSourceInitDict)
        this.addEventListener('message', () => {
          messageCount += 1
        })
      }
    }
    ;(window as unknown as Record<string, unknown>).EventSource =
      StressEventSource
    ;(window as unknown as Record<string, unknown>).__stressGetSseMessageCount =
      () => messageCount
  })

  const url = `${baseUrl}/workspaces/${workspaceId}`
  page.setDefaultTimeout(2000)
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await expect(page).toHaveURL(/\/workspaces\//)

  const endTime = Date.now() + durationSeconds * 1000
  const sampleIntervalMs = 1000
  let lastSample = Date.now()

  while (Date.now() < endTime) {
    const clickLatency = await withProbeTimeout(
      measureClickLatency(page),
      3000,
      'click latency'
    )
    if (clickLatency.ok) {
      metrics.clickLatenciesMs.push(clickLatency.value)
    } else {
      metrics.errors.push(clickLatency.error)
    }

    const scrollLatency = await withProbeTimeout(
      measureScrollLatency(page),
      3000,
      'scroll latency'
    )
    if (scrollLatency.ok) {
      metrics.scrollLatenciesMs.push(scrollLatency.value)
    } else {
      metrics.errors.push(scrollLatency.error)
    }

    // Sample JS heap size when the API is available.
    const memoryResult = await withProbeTimeout(
      page.evaluate(() => {
        const perf = (window as unknown as Record<string, unknown>).performance
        const mem = (perf as Record<string, unknown>)?.memory as
          | { usedJSHeapSize?: number }
          | undefined
        return mem?.usedJSHeapSize ?? 0
      }),
      1000,
      'memory probe'
    )
    if (memoryResult.ok) {
      const memoryMb = memoryResult.value / (1024 * 1024)
      metrics.memorySamplesMb.push(memoryMb)
      metrics.memoryHighWaterMb = Math.max(metrics.memoryHighWaterMb, memoryMb)
    } else {
      metrics.errors.push(memoryResult.error)
    }

    const now = Date.now()
    if (now - lastSample >= sampleIntervalMs) {
      const longTasksResult = await withProbeTimeout(
        page.evaluate(() => {
          return (
            ((window as unknown as Record<string, unknown>)
              .__stressLongTasks as PerformanceEntry[] | undefined) || []
          )
        }),
        1000,
        'long task probe'
      )
      if (longTasksResult.ok) {
        const longTasks = longTasksResult.value
        metrics.longTaskCount = longTasks.length
        metrics.longTaskTotalDurationMs = longTasks.reduce(
          (sum, entry) => sum + entry.duration,
          0
        )
      } else {
        metrics.errors.push(longTasksResult.error)
      }
      lastSample = now
    }

    await page.waitForTimeout(250)
  }

  const sseMessageCountResult = await withProbeTimeout(
    page.evaluate(() => {
      const getter = (window as unknown as Record<string, unknown>)
        .__stressGetSseMessageCount as (() => number) | undefined
      return getter ? getter() : 0
    }),
    1000,
    'sse message count probe'
  )
  const sseMessageCount = sseMessageCountResult.ok
    ? sseMessageCountResult.value
    : 0
  if (!sseMessageCountResult.ok) {
    metrics.errors.push(sseMessageCountResult.error)
  }
  metrics.sseMessagesReceived = sseMessageCount
  metrics.sseMessagesPerSecond = sseMessageCount / Math.max(1, durationSeconds)

  fs.mkdirSync(resultsDir, { recursive: true })
  const metricsPath = path.join(resultsDir, 'frontend-metrics.json')
  fs.writeFileSync(
    metricsPath,
    JSON.stringify(summarizeMetrics(metrics), null, 2)
  )

  expect(metrics.errors).toHaveLength(0)
  expect(percentile(metrics.clickLatenciesMs, 0.95)).toBeLessThan(300)
})
