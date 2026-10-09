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
        // 与生产宿主文档头（server/app/http_csp.py build_spa_csp）同形：
        // #1146 起含 media-src 'self' data: blob:——srcdoc 面板继承该策略，
        // 媒体播放的收紧层是面板自身 meta（media-src blob:）。
        'content-security-policy': `default-src 'self'; ${scriptSrc}; frame-src 'self' blob: data:; media-src 'self' data: blob:`,
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

/**
 * #1146：面板 meta 策略的 media-src blob: 放行面板自建 blob 的媒体。
 *
 * 面板脚本把一段真实可解码的 WAV（帧内字节构造，无网络）包成 blob URL
 * 喂 <audio>：宿主策略（buildPanelCsp 注入的 media-src blob:）下应真实
 * 加载（loadedmetadata）；bundle 自带 media-src 'none' meta 时（多策略
 * 取交集）应被拦并上报 media-src 违规——负例证明探针与断言非空转。
 * 宿主文档头带生产同形的 media-src 'self' data: blob:（srcdoc 继承层）。
 */
test('media-src blob: 放行面板自建 blob 的 <audio>/<video>（#1146，Chromium 实测）', async ({
  page,
}) => {
  await serveHost(page, await panelLib(), `script-src 'self' 'nonce-${H}'`)

  const mediaBundle = (blockMedia: boolean) => `<!doctype html><html><head>${
    blockMedia
      ? `<meta http-equiv="Content-Security-Policy" content="media-src 'none'">`
      : ''
  }</head><body>
<audio id="a"></audio><video id="v" controls muted></video>
<script id="t">
(function () {
  function report(msg) { parent.postMessage(Object.assign({ __mediaProbe: 1 }, msg), '*') }
  document.addEventListener('securitypolicyviolation', function (e) {
    report({ type: 'media-violation', directive: e.violatedDirective })
  })
  var numSamples = 1600
  var buf = new ArrayBuffer(44 + numSamples * 2)
  var view = new DataView(buf)
  function wstr(o, s) { for (var i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)) }
  wstr(0, 'RIFF'); view.setUint32(4, 36 + numSamples * 2, true); wstr(8, 'WAVE')
  wstr(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true)
  view.setUint16(22, 1, true); view.setUint32(24, 8000, true)
  view.setUint32(28, 16000, true); view.setUint16(32, 2, true)
  view.setUint16(34, 16, true); wstr(36, 'data'); view.setUint32(40, numSamples * 2, true)
  for (var i = 0; i < numSamples; i++) view.setInt16(44 + i * 2, Math.round(Math.sin(i / 20) * 8000), true)
  var url = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }))
  var a = document.getElementById('a')
  a.addEventListener('loadedmetadata', function () { report({ type: 'media-loaded' }) })
  a.src = url
  document.getElementById('v').src = URL.createObjectURL(
    new Blob([new Uint8Array([0, 0, 0, 1])], { type: 'video/mp4' })
  )
})()
</script></body></html>`

  const run = (html: string) =>
    page.evaluate(
      async ([html, nonce]) => {
        const result = { violations: [] as string[], loaded: false }
        const onMessage = (event: MessageEvent) => {
          const data = event.data as {
            __mediaProbe?: number
            type?: string
            directive?: string
          }
          if (!data || data.__mediaProbe !== 1) return
          if (data.type === 'media-loaded') result.loaded = true
          if (data.type === 'media-violation' && data.directive)
            result.violations.push(data.directive)
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
        await new Promise((r) => setTimeout(r, 400))
        frame.remove()
        window.removeEventListener('message', onMessage)
        return result
      },
      [html, H] as const
    )

  const allowed = await run(mediaBundle(false))
  expect(allowed.violations).toEqual([])
  expect(allowed.loaded).toBe(true)

  const blocked = await run(mediaBundle(true))
  expect(blocked.violations).toContain('media-src')
  expect(blocked.loaded).toBe(false)
})
