/**
 * FormattedJsonBody 的词法着色器单测（#255 审核 P1 回归钉）。
 *
 * 旧实现用单条全局正则做字符串分支——未闭合字符串（截断 JSON 的常态）
 * 会让它从每个引号位重试，O(K·L) 超线性：实测 512KB 截断内容 10-120s
 * 主线程冻结。lexJsonish 改单趟手写扫描后，这些形态必须全部在限时内
 * 完成（防退化回正则形态）。
 */
import { describe, expect, it, vi } from 'vitest'

// 组件内部用 styles（CSS module）——测试里同构一个恒等代理即可。
vi.mock('./previewRenderers.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { FormattedJsonBody } from './previewTextBodies'

function renderMarkup(content: string): string {
  const { formatted } = renderFormatted(content)
  return formatted
}

// 直接驱动组件的 useMemo 等价路径：挂载渲染一次取 innerHTML。
import { render } from '@testing-library/react'
function renderFormatted(content: string): { formatted: string } {
  const { container } = render(
    <FormattedJsonBody
      content={content}
      truncated={false}
      total={content.length}
    />
  )
  return { formatted: container.querySelector('pre')?.innerHTML ?? '' }
}

describe('FormattedJsonBody lexing', () => {
  it('colors keys, strings, and numbers (closed JSON)', () => {
    const html = renderMarkup('{"a": 1, "b": "x"}')
    expect(html).toContain('jsonKey')
    expect(html).toContain('jsonString')
    expect(html).toContain('jsonNumber')
  })

  it('preserves content semantically through the coloring (adversarial escapes)', () => {
    // 可解析内容先 pretty-print 再着色——textContent 是等价重排的
    // JSON；断言 parse 回来深度相等（着色不吞不改），且危险子串不被
    // 解释为标签。
    const raw = '{"s": "</pre><img onerror=\\"x\\"> & <>", "n": -1.5}'
    const html = renderMarkup(raw)
    const host = document.createElement('div')
    host.innerHTML = html
    expect(JSON.parse(host.textContent!)).toEqual(JSON.parse(raw))
    expect(host.querySelector('img')).toBeNull()
  })

  it('indentJsonish indents truncated JSON by bracket depth (string-aware)', async () => {
    // codex P2：解析失败/截断的 JSON 不再是超长单行——括号边界断行缩
    // 进；字符串内的括号/逗号不参与（截断的未闭合字符串吞到文末）。
    const { indentJsonish } = await import('./previewTextIndent')
    const truncated = '{"a": [{"b": "x{y,z", "c": 1, "d": "unterminated'
    const out = indentJsonish(truncated)
    // 三个开括号 → 至少出现深度 0/1/2 三档缩进行。
    expect(out).toMatch(/^\{\n {2}"a": \[\n {4}\{/m)
    // 字符串内的 {y,z 原样保留（不被当结构括号）。
    expect(out).toContain('"x{y,z"')
    // 性能（与 lexer 同纪律）：64KB 高转义未闭合内容在 1s 内完成。
    const dense = '"' + '\\"'.repeat(32_000)
    const start = performance.now()
    indentJsonish(`{"k": "${dense.slice(1)}`)
    expect(performance.now() - start).toBeLessThan(1000)
  })

  it('hyphens between letters stay un-colored (not-json)', () => {
    const html = renderMarkup('not-json{{')
    expect(html).not.toContain('span')
  })

  it('unterminated string colors to EOF without quadratic retries (truncated JSON)', () => {
    // 审核实测的冻结形态：大量转义引号 + 未闭合字符串。限时断言：
    // 64KB 的这种内容必须在 1s 内完成（旧正则同尺寸已是秒级冻结）。
    const dense = '"' + '\\"'.repeat(32_000) // 64KB 未闭合高转义
    const start = performance.now()
    const html = renderMarkup(`{"k": "${dense.slice(1)}`)
    const ms = performance.now() - start
    expect(ms).toBeLessThan(1000)
    expect(html).toContain('jsonString')
    const host = document.createElement('div')
    host.innerHTML = html
    expect(host.textContent?.length).toBeGreaterThan(60_000)
  })
})
