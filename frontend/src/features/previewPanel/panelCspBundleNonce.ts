/**
 * 预览面板 bundle 脚本的 nonce 盖章（#989，拆自 panelCsp.ts）。
 *
 * 面板脚本要同时过两层：继承的宿主头策略（严格模式 `'self' 'nonce-H'`；
 * 兼容模式 `'self' 'unsafe-inline'`）与 bundle 自带、对该脚本生效的 CSP
 * 策略（0..n 条，全部要过）。一个脚本只能带一个 nonce，按下面规则二选一：
 * - bundle 策略放行 H（无生效策略、生效指令全放行 inline 等）→ 盖 H：
 *   两种模式都运行；
 * - 否则若脚本自带 nonce 且 bundle 策略放行它 → 保留原 nonce。inline 脚本：
 *   兼容模式运行，严格模式被宿主拦截（探针提示），盖 H 也会被 bundle 拦，
 *   结果不变——此形态严格模式不支持（作者约束见 server/app/mcp_server/
 *   preview_guide.md）。外链 `<script src>`（面板 meta 只放行平台 origin）：
 *   宿主严格头以 'self' 放行，保留后两种模式都运行；
 * - 否则盖 H：bundle 本就拦它，两种模式结果不变（外链脚本若被 bundle 按
 *   URL 放行，盖 H 后照常运行）。
 * 生效策略按浏览器语义取：只认 <head> 直接子元素的 CSP meta（body、head
 * <noscript> 内与 Report-Only 不计），且只管文档顺序在它之后的脚本；放行
 * 与否只看对 <script> 生效的指令（解析规则见 panelCspDirectives.ts）。
 * 已知偏差：bundle 同时以 nonce 与内容 hash 放行同一 inline 脚本时按 nonce
 * 保留，严格模式下被拦（盖 H 本可运行）——仍落在上面"自带 nonce 策略仅
 * 兼容模式可用"的文档约束内。
 *
 * 安全前提：保留 bundle nonce 不放宽严格模式——宿主严格头只认 H，这由
 * 浏览器执行继承的宿主策略保证，单测（jsdom 无 CSP）钉不住。决策表在
 * frontend/e2e/previewCspMatrix.ts，vitest（panelCspBundleNonce.test.ts）
 * 断言盖章决策；strict / compat 两列运行结果由可选 Chromium 矩阵核对：
 * `cd frontend && E2E_CSP_MATRIX=1 npx playwright test -c
 * playwright.e2e.config.ts preview-csp-matrix`（无需后端）。
 */
import { allowsInlineScript, scriptElemPolicies } from './panelCspDirectives'

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
    for (const sources of scriptElemPolicies(
      meta.getAttribute('content') ?? ''
    ))
      policies.push([meta, sources])
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
