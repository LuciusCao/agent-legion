/**
 * panelCsp 契约（#989）：宿主 nonce 读取（占位符视为无）、bundle 脚本盖章
 * 走解析器语义、无 nonce 时零改动（不盖章、不注入探针）。
 */
import { afterEach, describe, expect, it } from 'vitest'
import {
  buildPanelCsp,
  CSP_NONCE_PLACEHOLDER,
  injectPanelCsp,
  readDocumentCspNonce,
} from './panelCsp'

function parse(html: string): Document {
  return new DOMParser().parseFromString(html, 'text/html')
}

afterEach(() => {
  document
    .querySelectorAll('meta[property="csp-nonce"]')
    .forEach((meta) => meta.remove())
})

describe('readDocumentCspNonce', () => {
  it('读 meta[property=csp-nonce] 的 nonce；缺失为空串', () => {
    expect(readDocumentCspNonce()).toBe('')
    const meta = document.createElement('meta')
    meta.setAttribute('property', 'csp-nonce')
    meta.setAttribute('nonce', 'abc123')
    document.head.appendChild(meta)
    expect(readDocumentCspNonce()).toBe('abc123')
  })

  it('vite dev/preview 未替换的占位符视为无 nonce', () => {
    const doc = parse(
      `<html><head><meta property="csp-nonce" nonce="${CSP_NONCE_PLACEHOLDER}"></head></html>`
    )
    expect(readDocumentCspNonce(doc)).toBe('')
  })
})

describe('injectPanelCsp', () => {
  const csp = buildPanelCsp()

  it('给每个真实 <script> 盖 nonce，并在 CSP meta 之后注入带 nonce 的探针', () => {
    const out = parse(
      injectPanelCsp(
        '<!doctype html><html><head><script>var s = "<script>"</script></head>' +
          '<body><script type="module">go()</script><svg><script>x()</script></svg></body></html>',
        csp,
        'n0nce'
      )
    )
    const head = Array.from(out.head.children)
    expect(head[0].getAttribute('http-equiv')).toBe('Content-Security-Policy')
    expect(head[1].tagName).toBe('SCRIPT')
    expect(head[1].textContent).toContain('securitypolicyviolation')
    expect(head[1].textContent).toContain("type:'csp-violation'")
    const scripts = Array.from(out.querySelectorAll('script'))
    // 探针 + head 脚本 + body module 脚本 + svg 脚本；字符串里的伪 <script> 不算。
    expect(scripts).toHaveLength(4)
    for (const script of scripts) {
      expect(script.getAttribute('nonce')).toBe('n0nce')
    }
  })

  it('bundle 自带 nonce 策略的脚本保留原 nonce（兼容模式可恢复），其余照常盖章', () => {
    const out = parse(
      injectPanelCsp(
        '<!doctype html><html><head>' +
          `<meta http-equiv="Content-Security-Policy" content="script-src 'nonce-panel'">` +
          '<script nonce="panel">own()</script><script nonce="stale">x()</script>' +
          '</head><body><script>y()</script></body></html>',
        csp,
        'n0nce'
      )
    )
    const nonces = Array.from(out.querySelectorAll('script')).map((s) =>
      s.getAttribute('nonce')
    )
    // 探针、bundle 自有 nonce 脚本、无匹配策略的 nonce、无 nonce 脚本。
    expect(nonces).toEqual(['n0nce', 'panel', 'n0nce', 'n0nce'])
  })

  it('只认对脚本实际生效的 bundle 策略：body 内、head noscript 内、脚本之后的 meta 不触发保留', () => {
    const out = parse(
      injectPanelCsp(
        '<!doctype html><html><head>' +
          '<noscript><meta http-equiv="Content-Security-Policy" content="script-src \'nonce-ns\'"></noscript>' +
          '<script nonce="late">a()</script><script nonce="ns">n()</script>' +
          `<meta http-equiv="Content-Security-Policy" content="script-src 'nonce-late'">` +
          '</head><body>' +
          `<meta http-equiv="Content-Security-Policy" content="script-src 'nonce-body'">` +
          '<script nonce="body">b()</script></body></html>',
        csp,
        'n0nce'
      )
    )
    const nonces = Array.from(out.querySelectorAll('script')).map((s) =>
      s.getAttribute('nonce')
    )
    expect(nonces).toEqual(['n0nce', 'n0nce', 'n0nce', 'n0nce'])
  })

  it('CSP 关键字大小写不敏感：NONCE-x 声明同样触发保留', () => {
    const out = parse(
      injectPanelCsp(
        '<!doctype html><html><head>' +
          `<meta http-equiv="content-security-policy" content="SCRIPT-SRC 'NONCE-Pa1'">` +
          '<script nonce="Pa1">own()</script></head><body></body></html>',
        csp,
        'n0nce'
      )
    )
    const nonces = Array.from(out.querySelectorAll('script')).map((s) =>
      s.getAttribute('nonce')
    )
    expect(nonces).toEqual(['n0nce', 'Pa1'])
  })

  it('无 nonce 时只注入 CSP meta，脚本原样', () => {
    const out = injectPanelCsp(
      '<!doctype html><html><head><script>var a=1</script></head><body></body></html>',
      csp
    )
    expect(out).not.toContain('nonce=')
    expect(out).not.toContain('securitypolicyviolation')
    expect(out).toContain('<script>var a=1</script>')
  })

  it('面板 meta 策略保持 unsafe-inline 不含 nonce（实例回退开关依赖它）', () => {
    expect(csp).toContain("script-src 'unsafe-inline'")
    expect(csp).not.toContain('nonce-')
  })
})
