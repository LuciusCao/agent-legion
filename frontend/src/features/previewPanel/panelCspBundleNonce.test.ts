/**
 * bundle nonce 兼容契约决策表（#989）的盖章断言。表本体与 strict / compat
 * 列的含义见 frontend/e2e/previewCspMatrix.ts（与可选 Chromium 矩阵
 * preview-csp-matrix.spec.ts 共用同一张表、同一脚本体）；规则见
 * panelCspBundleNonce.ts 文件头。
 */
import { describe, expect, it } from 'vitest'
import { bundleHtml, H, ROWS } from '../../../e2e/previewCspMatrix'
import { buildPanelCsp, injectPanelCsp } from './panelCsp'

const parse = (html: string) =>
  new DOMParser().parseFromString(html, 'text/html')

describe('bundle nonce 兼容契约决策表', () => {
  it.each(ROWS)('$form → $stamp（strict $strict / compat $compat）', (row) => {
    const html = bundleHtml(row)
    const own = parse(html).getElementById('t')?.getAttribute('nonce')
    const out = parse(injectPanelCsp(html, buildPanelCsp(), H))
    const stamped = out.getElementById('t')?.getAttribute('nonce')
    expect(stamped).toBe(row.stamp === 'own' ? own : H)
  })

  it('inline 脚本保留原 nonce 只发生在严格模式本就拦截、兼容模式可恢复的行', () => {
    const kept = ROWS.filter((r) => r.stamp === 'own' && !r.external)
    expect(kept.length).toBeGreaterThan(0)
    for (const row of kept) {
      expect([row.form, row.strict, row.compat]).toEqual([
        row.form,
        'blocked',
        'run',
      ])
    }
  })

  it('外链脚本保留原 nonce 时两种模式都运行（宿主严格头以 self 放行）', () => {
    const kept = ROWS.filter((r) => r.stamp === 'own' && r.external)
    expect(kept.length).toBeGreaterThan(0)
    for (const row of kept) {
      expect([row.form, row.strict, row.compat]).toEqual([
        row.form,
        'run',
        'run',
      ])
    }
  })
})
