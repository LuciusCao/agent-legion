import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { build, type Rollup } from 'vite'

import { BODY, bundleHtml, H, ORIGIN, ROWS } from './previewCspMatrix'

/**
 * #989：bundle nonce 兼容契约决策表的 Chromium 实测（可选，无需后端）。
 *
 * jsdom 不执行 CSP，vitest 只能断言盖章决策；这里把同一张表
 * （previewCspMatrix.ts）放进真实浏览器：宿主页由 page.route 伪造，按
 * strict（`'self' 'nonce-H'`）/ compat（`'self' 'unsafe-inline'`）下发文档
 * CSP 头，面板走生产 injectPanelCsp 后以 srcdoc + sandbox="allow-scripts"
 * 渲染，断言目标脚本是否运行、违规探针是否上报。表里的 strict / compat
 * 两列（以及"宿主严格头只认 H"这一安全前提）由此核对。
 *
 * 运行：cd frontend && E2E_CSP_MATRIX=1 npx playwright test -c
 * playwright.e2e.config.ts preview-csp-matrix
 */
test.skip(
  !process.env.E2E_CSP_MATRIX,
  'opt-in：E2E_CSP_MATRIX=1（无需后端的 Chromium CSP 矩阵）'
)
test.skip(
  ({ browserName }) => browserName !== 'chromium',
  '表中解析细节以 Chromium 实测为准'
)

async function panelLib(): Promise<string> {
  const entry = fileURLToPath(
    new URL('../src/features/previewPanel/panelCsp.ts', import.meta.url)
  )
  const out = (await build({
    configFile: false,
    logLevel: 'silent',
    build: {
      write: false,
      minify: false,
      lib: { entry, formats: ['iife'], name: 'PanelCsp' },
    },
  })) as Rollup.RollupOutput | Rollup.RollupOutput[]
  return (Array.isArray(out) ? out[0] : out).output[0].code
}

async function serveHost(page: Page, lib: string, scriptSrc: string) {
  await page.route(`${ORIGIN}/**`, (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/lib.js')
      return route.fulfill({ body: lib, contentType: 'text/javascript' })
    if (path === '/ext.js')
      return route.fulfill({ body: BODY, contentType: 'text/javascript' })
    return route.fulfill({
      contentType: 'text/html',
      headers: {
        'content-security-policy': `default-src 'self'; ${scriptSrc}; frame-src 'self' blob: data:`,
      },
      body: '<!doctype html><html><head></head><body><script src="/lib.js"></script></body></html>',
    })
  })
  await page.goto(`${ORIGIN}/`)
}

for (const mode of ['strict', 'compat'] as const) {
  test(`决策表 ${mode} 列（Chromium 实测）`, async ({ page }) => {
    const scriptSrc =
      mode === 'strict'
        ? `script-src 'self' 'nonce-${H}'`
        : "script-src 'self' 'unsafe-inline'"
    await serveHost(page, await panelLib(), scriptSrc)
    for (const row of ROWS) {
      const got = await page.evaluate(
        async ([html, nonce]) => {
          const result = { ran: false, probe: false }
          const onMessage = (event: MessageEvent) => {
            if (event.data === 'ran') result.ran = true
            else if (event.data?.type === 'csp-violation') result.probe = true
          }
          window.addEventListener('message', onMessage)
          const lib = (
            window as unknown as {
              PanelCsp: {
                buildPanelCsp(): string
                injectPanelCsp(h: string, csp: string, n: string): string
              }
            }
          ).PanelCsp
          const frame = document.createElement('iframe')
          frame.setAttribute('sandbox', 'allow-scripts')
          frame.srcdoc = lib.injectPanelCsp(html, lib.buildPanelCsp(), nonce)
          const loaded = new Promise((r) => frame.addEventListener('load', r))
          document.body.appendChild(frame)
          await loaded
          await new Promise((r) => setTimeout(r, 150))
          frame.remove()
          window.removeEventListener('message', onMessage)
          return result
        },
        [bundleHtml(row), H] as const
      )
      const ran = row[mode] === 'run'
      expect.soft(got, `${row.form}`).toEqual({ ran, probe: !ran })
    }
  })
}
