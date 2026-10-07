/**
 * bundle 自带 CSP meta 的最小指令解析（#989，供 panelCspBundleNonce.ts
 * 判断 bundle 策略是否放行某个 nonce 的 <script>）。只实现脚本元素判定所需
 * 的 CSP3 子集：
 * - 解析（CSP3 §2.2.1 parse a serialized CSP）：按 `;` 切分、去 ASCII 空白、
 *   指令名 ASCII 小写、重复指令只认首项、源列表按 ASCII 空白切分；
 * - 生效指令（§6.8.1 effective directive + fallback list）：<script> 元素
 *   依次取 script-src-elem → script-src → default-src，取到即止（取到空
 *   列表也止步，等同 'none'）；style-src 等其他指令与脚本无关；
 * - inline 放行（§6.7.3 / §6.7.2.3）：源列表的 `'nonce-…'` 与脚本 nonce
 *   相同即放行；否则仅当含 'unsafe-inline' 且无任何 nonce/hash 源、无
 *   'strict-dynamic' 时放行全部 inline。关键字与 `nonce-` 前缀大小写不敏感，
 *   nonce 值原样比较。
 * hash 源（'sha256-…'）只计入"使 'unsafe-inline' 失效"，不做内容匹配——
 * 同步注入路径算不了摘要；由此带来的唯一偏差见 panelCspBundleNonce.ts。
 */

const ASCII_WS = /[\t\n\f\r ]+/
const ASCII_WS_EDGES = /^[\t\n\f\r ]+|[\t\n\f\r ]+$/g
const NONCE_SOURCE = /^'nonce-([A-Za-z0-9+/_-]+={0,2})'$/i
const HASH_SOURCE = /^'sha(?:256|384|512)-[A-Za-z0-9+/_-]+={0,2}'$/i
const SCRIPT_ELEM_FALLBACK = ['script-src-elem', 'script-src', 'default-src']

/** 一条策略里对 <script> 元素生效的源列表；null = 策略不管脚本元素。 */
export function scriptElemSources(serialized: string): string[] | null {
  const directives = new Map<string, string[]>()
  for (const token of serialized.split(';')) {
    const parts = token.replace(ASCII_WS_EDGES, '').split(ASCII_WS)
    const name = parts[0].toLowerCase()
    if (!name || directives.has(name)) continue
    directives.set(name, parts.slice(1))
  }
  for (const name of SCRIPT_ELEM_FALLBACK) {
    const sources = directives.get(name)
    if (sources !== undefined) return sources
  }
  return null
}

/** 生效源列表是否放行带该 nonce 的 inline <script>。 */
export function allowsInlineScript(
  sources: string[] | null,
  nonce: string
): boolean {
  if (sources === null) return true
  let nonceOrHash = false
  let unsafeInline = false
  let strictDynamic = false
  for (const source of sources) {
    const declared = NONCE_SOURCE.exec(source)
    if (declared?.[1] === nonce) return true
    if (declared || HASH_SOURCE.test(source)) nonceOrHash = true
    const keyword = source.toLowerCase()
    if (keyword === "'unsafe-inline'") unsafeInline = true
    if (keyword === "'strict-dynamic'") strictDynamic = true
  }
  return unsafeInline && !nonceOrHash && !strictDynamic
}
