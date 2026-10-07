/**
 * 预览面板 bundle nonce 兼容契约决策表（#989，盖章规则见
 * src/features/previewPanel/panelCspBundleNonce.ts 文件头）。
 *
 * 每行：bundle 自带 CSP 形态 × 目标脚本（id="t"，inline 或外链）的 nonce
 * 形态 → 宿主是否盖章（host = 盖宿主 nonce H，own = 保留原 nonce），以及
 * 两种实例模式下目标脚本是否运行（strict = 宿主头 `script-src 'self'
 * 'nonce-H'`；compat = `'self' 'unsafe-inline'`）。违规探针（宿主提示）
 * 恰在脚本被拦时触发：探针落在 bundle meta 之前、带 H，两种模式都运行，
 * 任一层拦截都会上报 script-src-elem 违规。
 *
 * 两处消费同一张表：panelCspBundleNonce.test.ts（vitest）断言盖章决策；
 * preview-csp-matrix.spec.ts（可选，Chromium）断言 strict / compat 两列与
 * 探针。形态与规范有出入处以 Chromium 实测为准。
 */
import { createHash } from 'node:crypto'

export const H = 'hostNonce1'
/** 矩阵页面的 origin（宿主 'self' 与面板 meta 的平台 origin）。 */
export const ORIGIN = 'http://csp-matrix.test'
/** 目标脚本体：inline 脚本与外链 ext.js 共用，向宿主报告"已运行"。 */
export const BODY = "parent.postMessage('ran','*')"
const SHA = `'sha256-${createHash('sha256').update(BODY).digest('base64')}'`
const EXT = `${ORIGIN}/ext.js`

const meta = (content: string, equiv = 'Content-Security-Policy') =>
  `<meta http-equiv="${equiv}" content="${content}">`
const target = (nonce?: string, src?: string) =>
  `<script id="t"${src ? ` src="${src}"` : ''}${
    nonce === undefined ? '' : ` nonce="${nonce}"`
  }>${src ? '' : BODY}</script>`

export type Outcome = 'run' | 'blocked'
export interface Row {
  form: string
  head: string
  body?: string
  /** 目标脚本是外链 `<script src>`。 */
  external?: boolean
  stamp: 'host' | 'own'
  strict: Outcome
  compat: Outcome
}

const RUN = { strict: 'run', compat: 'run' } as const
const BLOCKED = { strict: 'blocked', compat: 'blocked' } as const
const COMPAT_ONLY = { strict: 'blocked', compat: 'run' } as const
const N = "'nonce-p'"
const U = "'unsafe-inline'"

export const ROWS: Row[] = [
  // 无 bundle CSP：一律盖 H。
  { form: '无 CSP / 无 nonce', head: target(), stamp: 'host', ...RUN },
  { form: '无 CSP / 自带 nonce', head: target('p'), stamp: 'host', ...RUN },
  // 仅 'unsafe-inline'：bundle 放行任何 inline，盖 H 两模式都跑。
  {
    form: `script-src ${U} / 无 nonce`,
    head: meta(`script-src ${U}`) + target(),
    stamp: 'host',
    ...RUN,
  },
  {
    form: `script-src ${U} / 自带 nonce`,
    head: meta(`script-src ${U}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  // script-src nonce：匹配则保留（兼容可恢复），否则盖 H（bundle 本就拦）。
  {
    form: 'script-src nonce / 匹配',
    head: meta(`script-src ${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: 'script-src nonce / 无 nonce',
    head: meta(`script-src ${N}`) + target(),
    stamp: 'host',
    ...BLOCKED,
  },
  {
    form: 'script-src nonce / 不匹配',
    head: meta(`script-src ${N}`) + target('q'),
    stamp: 'host',
    ...BLOCKED,
  },
  // 生效指令选择：script-src-elem 覆盖 script-src，default-src 仅作回退。
  {
    form: `script-src-elem ${U} 覆盖 script-src nonce`,
    head: meta(`script-src-elem ${U}; script-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: `script-src-elem nonce 覆盖 script-src ${U}`,
    head: meta(`script-src ${U}; script-src-elem ${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '空 script-src-elem（= none）覆盖 script-src nonce',
    head: meta(`script-src-elem; script-src ${N}`) + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  {
    form: '只有 default-src nonce / 匹配',
    head: meta(`default-src ${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: `default-src nonce 被 script-src ${U} 覆盖`,
    head: meta(`default-src ${N}; script-src ${U}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  // nonce 仅在 style-src：与脚本无关。
  {
    form: `nonce 仅在 style-src（script-src ${U}）`,
    head: meta(`script-src ${U}; style-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: 'nonce 仅在 style-src（无脚本指令）',
    head: meta(`style-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  // 多条 meta：全部生效策略都要过。
  {
    form: `多条 meta：nonce + ${U}`,
    head: meta(`script-src ${N}`) + meta(`script-src ${U}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '多条 meta：nonce-p + nonce-q',
    head: meta(`script-src ${N}`) + meta("script-src 'nonce-q'") + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  {
    form: `多条 meta：${U} + 仅 style-src nonce`,
    head: meta(`script-src ${U}`) + meta(`style-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  // 一条 meta 内逗号分隔多条策略（Chromium 实测按逗号拆分，每条都要过）。
  {
    form: `逗号多策略：nonce, ${U}`,
    head: meta(`script-src ${N}, script-src ${U}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '逗号多策略：img-src, script-src nonce',
    head: meta(`img-src x, script-src ${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: `逗号多策略：${U}, 仅 style-src nonce`,
    head: meta(`script-src ${U}, style-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: '逗号多策略：nonce-p, nonce-q',
    head: meta(`script-src ${N}, script-src 'nonce-q'`) + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  // meta 位置：只有 head 直接子、且在脚本之前的才生效。
  {
    form: 'meta 在 head、脚本在 body',
    head: meta(`script-src ${N}`),
    body: target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: 'meta 在脚本之后',
    head: target('p') + meta(`script-src ${N}`),
    stamp: 'host',
    ...RUN,
  },
  {
    form: 'meta 在 body',
    head: '',
    body: meta(`script-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: 'meta 在 head <noscript>',
    head: `<noscript>${meta(`script-src ${N}`)}</noscript>` + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: 'Report-Only meta（meta 不支持，不执行）',
    head:
      meta(`script-src ${N}`, 'Content-Security-Policy-Report-Only') +
      target('p'),
    stamp: 'host',
    ...RUN,
  },
  // 重复指令：首项生效（指令名先小写再判重）。
  {
    form: `重复 script-src：${U} 在前`,
    head: meta(`script-src ${U}; script-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: '重复 script-src：nonce 在前',
    head: meta(`script-src ${N}; script-src ${U}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '重复指令名大小写不同：首项生效',
    head: meta(`Script-Src ${U}; script-src ${N}`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  // 空白：\v 与 \f 同为分隔符（Chromium 实测）。
  {
    form: '\\v 分隔指令名与源',
    head: meta(`script-src\v${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '\\v 前导与源间分隔',
    head: meta(`\vscript-src ${U}\v${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  // 非法字符：值含非 ASCII / 控制字符的指令整条丢弃，但占住指令名（Chromium 实测）。
  {
    form: '指令值含非 ASCII：整条丢弃（不限制）',
    head: meta(`script-src ${N} é`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: '指令值含控制字符：整条丢弃',
    head: meta(`script-src ${N} \x01`) + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: '被丢弃的 script-src-elem 回退到 script-src nonce',
    head: meta(`script-src-elem ${U} é; script-src ${N}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: '被丢弃的指令占名：后续同名指令作废',
    head:
      meta(`script-src-elem ${U} é; script-src-elem ${N}; script-src ${U}`) +
      target('p'),
    stamp: 'host',
    ...RUN,
  },
  // 关键字/指令名大小写不敏感，nonce 值大小写敏感。
  {
    form: '大写指令名与 NONCE- 前缀',
    head: meta(`SCRIPT-SRC-ELEM 'NONCE-p'; script-src ${U}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: "大写 'UNSAFE-INLINE'",
    head: meta("script-src 'UNSAFE-INLINE'") + target('p'),
    stamp: 'host',
    ...RUN,
  },
  {
    form: 'nonce 值大小写不同',
    head: meta("script-src 'nonce-P'") + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  // hash 源：使 'unsafe-inline' 失效；内容 hash 匹配时 bundle 不看 nonce。
  {
    form: 'hash 匹配 / 无 nonce',
    head: meta(`script-src ${SHA}`) + target(),
    stamp: 'host',
    ...RUN,
  },
  {
    form: `hash 不匹配 + ${U}（被 hash 关掉）`,
    head: meta(`script-src ${U} 'sha256-AAAA'`) + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  // 已知偏差：nonce 与 hash 同时放行时按 nonce 保留，严格模式被拦（盖 H 本可运行）。
  {
    form: 'nonce 匹配 + hash 匹配（已知偏差）',
    head: meta(`script-src ${N} ${SHA}`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  // 'strict-dynamic'：使 'unsafe-inline' 失效，nonce 仍有效。
  {
    form: "'strict-dynamic' + nonce / 匹配",
    head: meta(`script-src ${N} 'strict-dynamic'`) + target('p'),
    stamp: 'own',
    ...COMPAT_ONLY,
  },
  {
    form: `'strict-dynamic' + ${U}`,
    head: meta(`script-src ${U} 'strict-dynamic'`) + target('p'),
    stamp: 'host',
    ...BLOCKED,
  },
  // 外链 <script src>（平台 origin）：宿主严格头以 'self' 放行，保留 own 两模式都跑。
  {
    form: '外链 / 无 CSP',
    head: target(undefined, EXT),
    external: true,
    stamp: 'host',
    ...RUN,
  },
  {
    form: '外链 / script-src nonce 匹配',
    head: meta(`script-src ${N}`) + target('p', EXT),
    external: true,
    stamp: 'own',
    ...RUN,
  },
  {
    form: '外链 / script-src nonce 无 nonce',
    head: meta(`script-src ${N}`) + target(undefined, EXT),
    external: true,
    stamp: 'host',
    ...BLOCKED,
  },
  {
    form: "外链 / 'strict-dynamic' + nonce 匹配",
    head: meta(`script-src ${N} 'strict-dynamic'`) + target('p', EXT),
    external: true,
    stamp: 'own',
    ...RUN,
  },
  {
    form: '外链 / bundle 按 URL 放行',
    head: meta(`script-src ${ORIGIN}`) + target('p', EXT),
    external: true,
    stamp: 'host',
    ...RUN,
  },
  {
    form: `外链 / script-src ${U}（不放行外链）`,
    head: meta(`script-src ${U}`) + target(undefined, EXT),
    external: true,
    stamp: 'host',
    ...BLOCKED,
  },
]

export function bundleHtml(row: Row): string {
  return `<!doctype html><html><head>${row.head}</head><body>${row.body ?? ''}</body></html>`
}
