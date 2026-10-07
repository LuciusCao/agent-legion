/**
 * 预览面板 bundle 脚本的 nonce 盖章（#989，拆自 panelCsp.ts）。
 *
 * 面板脚本要同时过两层：继承的宿主头策略（严格模式只认宿主 nonce H；兼容
 * 模式 'unsafe-inline' 全放行）与 bundle 自带、对该脚本生效的 CSP meta
 * （0..n 条，全部要过）。一个脚本只能带一个 nonce，按下面规则二选一：
 * - bundle 策略放行 H（无生效策略、生效指令全放行 inline 等）→ 盖 H：
 *   两种模式都运行；
 * - 否则若脚本自带 nonce 且 bundle 策略放行它 → 保留原 nonce：兼容模式
 *   运行；严格模式被宿主拦截（探针提示），盖 H 也会被 bundle 拦，结果
 *   不变——此形态严格模式不支持（作者约束见 server/app/mcp_server/
 *   preview_guide.md）；
 * - 否则盖 H：bundle 本就拦它，两种模式结果不变。
 * 生效策略按浏览器语义取：只认 <head> 直接子元素的 CSP meta（body、head
 * <noscript> 内与 Report-Only 不计），且只管文档顺序在它之后的脚本；放行
 * 与否只看对 <script> 生效的指令（解析规则见 panelCspDirectives.ts）。
 * 已知偏差：bundle 同时以 nonce 与内容 hash 放行同一脚本时按 nonce 保留，
 * 严格模式下被拦（盖 H 本可运行）——仍落在上面"自带 nonce 策略仅兼容模式
 * 可用"的文档约束内。完整决策表见 panelCspBundleNonce.test.ts。
 */
import { allowsInlineScript, scriptElemSources } from './panelCspDirectives'

type BundlePolicy = [Element, string[] | null]

/** bundle 自带、浏览器实际会执行的 CSP meta 及其脚本元素生效源列表。 */
function bundlePolicies(doc: Document): BundlePolicy[] {
  const policies: BundlePolicy[] = []
  for (const meta of Array.from(doc.head.children)) {
    if (
      meta.tagName !== 'META' ||
      meta.getAttribute('http-equiv')?.toLowerCase() !==
        'content-security-policy'
    )
      continue
    policies.push([meta, scriptElemSources(meta.getAttribute('content') ?? '')])
  }
  return policies
}

/** 位于脚本之前的全部 bundle 生效策略是否都放行带该 nonce 的脚本。 */
function bundleAllows(
  script: Element,
  nonce: string,
  policies: BundlePolicy[]
): boolean {
  return policies.every(
    ([meta, sources]) =>
      (meta.compareDocumentPosition(script) &
        Node.DOCUMENT_POSITION_FOLLOWING) ===
        0 || allowsInlineScript(sources, nonce)
  )
}

/** 给 bundle 文档内每个真实 <script> 盖宿主 nonce（规则见文件头）。 */
export function stampScriptNonces(doc: Document, nonce: string): void {
  const policies = bundlePolicies(doc)
  for (const script of Array.from(doc.querySelectorAll('script'))) {
    const own = script.getAttribute('nonce')
    if (
      own &&
      !bundleAllows(script, nonce, policies) &&
      bundleAllows(script, own, policies)
    )
      continue
    script.setAttribute('nonce', nonce)
  }
}
