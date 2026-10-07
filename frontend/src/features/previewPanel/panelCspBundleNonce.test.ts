/**
 * bundle nonce 兼容契约决策表（#989，规则见 panelCspBundleNonce.ts 文件头）。
 *
 * 每行：bundle 自带 CSP 形态 × 目标脚本（id="t"）的 nonce 形态 → 宿主是否
 * 盖章（host = 盖宿主 nonce H，own = 保留原 nonce），以及两种实例模式下
 * 目标脚本是否运行（strict = 宿主头 `script-src 'self' 'nonce-H'`；compat =
 * `'self' 'unsafe-inline'`）。违规探针（宿主提示）恰在脚本被拦时触发：
 * 探针落在 bundle meta 之前、带 H，两种模式都运行，任一层拦截都会上报
 * script-src-elem 违规。strict / compat 列按 CSP3 推导，已在 Chromium 里
 * 逐行实测；本测试断言盖章决策。
 */
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { buildPanelCsp, injectPanelCsp } from './panelCsp'

const H = 'hostNonce1'
const BODY = 'go()'
const SHA = `'sha256-${createHash('sha256').update(BODY).digest('base64')}'`
const meta = (content: string, equiv = 'Content-Security-Policy') =>
  `<meta http-equiv="${equiv}" content="${content}">`
const target = (nonce?: string) =>
  `<script id="t"${nonce === undefined ? '' : ` nonce="${nonce}"`}>${BODY}</script>`

type Outcome = 'run' | 'blocked'
interface Row {
  form: string
  head: string
  body?: string
  stamp: 'host' | 'own'
  strict: Outcome
  compat: Outcome
}

const ROWS: Row[] = [
  // 无 bundle CSP：一律盖 H。
  {
    form: '无 CSP / 无 nonce',
    head: target(),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: '无 CSP / 自带 nonce',
    head: target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // 仅 'unsafe-inline'：bundle 放行任何 inline，盖 H 两模式都跑。
  {
    form: "script-src 'unsafe-inline' / 无 nonce",
    head: meta("script-src 'unsafe-inline'") + target(),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: "script-src 'unsafe-inline' / 自带 nonce",
    head: meta("script-src 'unsafe-inline'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // script-src nonce：匹配则保留（兼容可恢复），否则盖 H（bundle 本就拦）。
  {
    form: 'script-src nonce / 匹配',
    head: meta("script-src 'nonce-p'") + target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: 'script-src nonce / 无 nonce',
    head: meta("script-src 'nonce-p'") + target(),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  {
    form: 'script-src nonce / 不匹配',
    head: meta("script-src 'nonce-p'") + target('q'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  // 生效指令选择：script-src-elem 覆盖 script-src，default-src 仅作回退。
  {
    form: "script-src-elem 'unsafe-inline' 覆盖 script-src nonce",
    head:
      meta("script-src-elem 'unsafe-inline'; script-src 'nonce-p'") +
      target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: "script-src-elem nonce 覆盖 script-src 'unsafe-inline'",
    head:
      meta("script-src 'unsafe-inline'; script-src-elem 'nonce-p'") +
      target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: '空 script-src-elem（= none）覆盖 script-src nonce',
    head: meta("script-src-elem; script-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  {
    form: '只有 default-src nonce / 匹配',
    head: meta("default-src 'nonce-p'") + target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: "default-src nonce 被 script-src 'unsafe-inline' 覆盖",
    head:
      meta("default-src 'nonce-p'; script-src 'unsafe-inline'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // nonce 仅在 style-src：与脚本无关。
  {
    form: "nonce 仅在 style-src（script-src 'unsafe-inline'）",
    head: meta("script-src 'unsafe-inline'; style-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: 'nonce 仅在 style-src（无脚本指令）',
    head: meta("style-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // 多条 meta：全部生效策略都要过。
  {
    form: "多条 meta：nonce + 'unsafe-inline'",
    head:
      meta("script-src 'nonce-p'") +
      meta("script-src 'unsafe-inline'") +
      target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: '多条 meta：nonce-p + nonce-q',
    head:
      meta("script-src 'nonce-p'") + meta("script-src 'nonce-q'") + target('p'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  {
    form: "多条 meta：'unsafe-inline' + 仅 style-src nonce",
    head:
      meta("script-src 'unsafe-inline'") +
      meta("style-src 'nonce-p'") +
      target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // meta 位置：只有 head 直接子、且在脚本之前的才生效。
  {
    form: 'meta 在 head、脚本在 body',
    head: meta("script-src 'nonce-p'"),
    body: target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: 'meta 在脚本之后',
    head: target('p') + meta("script-src 'nonce-p'"),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: 'meta 在 body',
    head: '',
    body: meta("script-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: 'meta 在 head <noscript>',
    head: `<noscript>${meta("script-src 'nonce-p'")}</noscript>` + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: 'Report-Only meta（meta 不支持，不执行）',
    head:
      meta("script-src 'nonce-p'", 'Content-Security-Policy-Report-Only') +
      target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // 重复指令：首项生效（指令名先小写再判重）。
  {
    form: "重复 script-src：'unsafe-inline' 在前",
    head:
      meta("script-src 'unsafe-inline'; script-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: '重复 script-src：nonce 在前',
    head:
      meta("script-src 'nonce-p'; script-src 'unsafe-inline'") + target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: '重复指令名大小写不同：首项生效',
    head:
      meta("Script-Src 'unsafe-inline'; script-src 'nonce-p'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  // 关键字/指令名大小写不敏感，nonce 值大小写敏感。
  {
    form: '大写指令名与 NONCE- 前缀',
    head:
      meta("SCRIPT-SRC-ELEM 'NONCE-p'; script-src 'unsafe-inline'") +
      target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: "大写 'UNSAFE-INLINE'",
    head: meta("script-src 'UNSAFE-INLINE'") + target('p'),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: 'nonce 值大小写不同',
    head: meta("script-src 'nonce-P'") + target('p'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  // hash 源：使 'unsafe-inline' 失效；内容 hash 匹配时 bundle 不看 nonce。
  {
    form: 'hash 匹配 / 无 nonce',
    head: meta(`script-src ${SHA}`) + target(),
    stamp: 'host',
    strict: 'run',
    compat: 'run',
  },
  {
    form: "hash 不匹配 + 'unsafe-inline'（被 hash 关掉）",
    head: meta("script-src 'unsafe-inline' 'sha256-AAAA'") + target('p'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
  // 已知偏差：nonce 与 hash 同时放行时按 nonce 保留，严格模式被拦（盖 H 本可运行）。
  {
    form: 'nonce 匹配 + hash 匹配（已知偏差）',
    head: meta(`script-src 'nonce-p' ${SHA}`) + target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  // 'strict-dynamic'：使 'unsafe-inline' 失效，nonce 仍有效。
  {
    form: "'strict-dynamic' + nonce / 匹配",
    head: meta("script-src 'nonce-p' 'strict-dynamic'") + target('p'),
    stamp: 'own',
    strict: 'blocked',
    compat: 'run',
  },
  {
    form: "'strict-dynamic' + 'unsafe-inline'",
    head: meta("script-src 'unsafe-inline' 'strict-dynamic'") + target('p'),
    stamp: 'host',
    strict: 'blocked',
    compat: 'blocked',
  },
]

describe('bundle nonce 兼容契约决策表', () => {
  it.each(ROWS)('$form → $stamp（strict $strict / compat $compat）', (row) => {
    const html = `<!doctype html><html><head>${row.head}</head><body>${row.body ?? ''}</body></html>`
    const out = new DOMParser().parseFromString(
      injectPanelCsp(html, buildPanelCsp(), H),
      'text/html'
    )
    const own = /<script id="t" nonce="([^"]*)"/.exec(html)?.[1]
    const stamped = out.getElementById('t')?.getAttribute('nonce')
    expect(stamped).toBe(row.stamp === 'own' ? own : H)
  })

  it('保留原 nonce 只发生在严格模式本就拦截、兼容模式可恢复的行', () => {
    for (const row of ROWS.filter((r) => r.stamp === 'own')) {
      expect([row.form, row.strict, row.compat]).toEqual([
        row.form,
        'blocked',
        'run',
      ])
    }
  })
})
