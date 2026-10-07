/**
 * bundle 自带 CSP meta 的最小指令解析（#989，供 panelCspBundleNonce.ts
 * 判断 bundle 策略是否放行某个 nonce 的 <script>）。只实现脚本元素判定所需
 * 的 CSP3 子集，与规范有出入处以 Chromium 实测为准（标"实测"）：
 * - 一条 meta 的 content 按 `,` 拆成多条策略，每条都要放行（实测，同
 *   HTTP 头语义）；
 * - 每条策略（CSP3 §2.2.1 parse a serialized CSP）：按 `;` 切分、去 ASCII
 *   空白（含 \v，实测）、指令名 ASCII 小写、重复指令只认首项、源列表按
 *   ASCII 空白切分；指令值含 ASCII 可打印字符与空白以外的字符（非 ASCII、
 *   控制字符）时整条指令丢弃，但仍占住指令名、使后续同名指令作废（实测）；
 * - 生效指令（§6.8.1 effective directive + fallback list）：<script> 元素
 *   依次取 script-src-elem → script-src → default-src，取到即止（取到空
 *   列表也止步，等同 'none'；被丢弃的指令视为缺席、继续回退）；style-src
 *   等其他指令与脚本无关；
 * - inline 放行（§6.7.3 / §6.7.2.3）：源列表的 `'nonce-…'` 与脚本 nonce
 *   相同即放行；否则仅当含 'unsafe-inline' 且无任何 nonce/hash 源、无
 *   'strict-dynamic' 时放行全部 inline。关键字与 `nonce-` 前缀大小写不敏感，
 *   nonce 值原样比较。
 * hash 源（'sha256-…'）只计入"使 'unsafe-inline' 失效"，不做内容匹配——
 * 同步注入路径算不了摘要；由此带来的唯一偏差见 panelCspBundleNonce.ts。
 * 直接单测见 panelCspDirectives.test.ts。
 */

const ASCII_WS = /[\t\n\v\f\r ]+/
const ASCII_WS_EDGES = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g
const VALUE_TOKEN = /^[\x21-\x7e]*$/
const NONCE_SOURCE = /^'nonce-([A-Za-z0-9+/_-]+={0,2})'$/i
const HASH_SOURCE = /^'sha(?:256|384|512)-[A-Za-z0-9+/_-]+={0,2}'$/i
const SCRIPT_ELEM_FALLBACK = ['script-src-elem', 'script-src', 'default-src']

/** 一条策略里对 <script> 元素生效的源列表；null = 策略不管脚本元素。 */
function scriptElemSources(policy: string): string[] | null {
  // undefined = 指令因非法字符被丢弃（占名不生效）。
  const directives = new Map<string, string[] | undefined>()
  for (const token of policy.split(';')) {
    const [head, ...value] = token.replace(ASCII_WS_EDGES, '').split(ASCII_WS)
    const name = head.replace(/[A-Z]/g, (c) => c.toLowerCase())
    if (!name || directives.has(name)) continue
    const valid = value.every((source) => VALUE_TOKEN.test(source))
    directives.set(name, valid ? value : undefined)
  }
  for (const name of SCRIPT_ELEM_FALLBACK) {
    const sources = directives.get(name)
    if (sources !== undefined) return sources
  }
  return null
}

/** meta content 拆出的每条策略对 <script> 元素生效的源列表。 */
export function scriptElemPolicies(content: string): (string[] | null)[] {
  return content.split(',').map(scriptElemSources)
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
