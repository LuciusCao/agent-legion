/**
 * 预览面板 bundle 脚本的 nonce 盖章（#989，拆自 panelCsp.ts）。
 *
 * 默认给每个真实 <script> 盖宿主 nonce；例外是 bundle 自带 nonce 策略的
 * 脚本（codex P2）：脚本带 nonce="X"，且在它之前有一条 head 直接子元素的
 * CSP meta 声明了 `'nonce-X'`（即浏览器对它实际执行的 bundle 策略）时保留
 * 原 nonce。一个脚本只能有一个 nonce，宿主 nonce 必被那条策略拒绝；保留后
 * 实例 CSP 兼容模式（宿主头 'unsafe-inline'）下照常运行，严格模式下覆盖与
 * 否都过不了两层策略，结果不变——此形态严格模式不支持（作者约束见
 * server/app/mcp_server/preview_guide.md）。不生效的 meta（body 内、head
 * <noscript> 内、脚本之后）不触发保留，脚本照常盖宿主 nonce。
 */

type BundlePolicy = [Element, Set<string>]

/**
 * bundle 自带、浏览器实际会执行的 CSP meta 及其声明的 `'nonce-…'` 值（在
 * 宿主 meta 插入前读取）。浏览器只认 <head> 直接子元素里的 CSP meta，且
 * 策略自 meta 解析起才生效——body / head <noscript> 内的 meta 不计，脚本
 * 之后的 meta 也管不到它（由 policyNonceFor 按文档顺序判断）。CSP 关键字
 * 大小写不敏感，nonce 值本身原样比较。
 */
function bundlePolicyNonces(doc: Document): BundlePolicy[] {
  const policies: BundlePolicy[] = []
  for (const meta of Array.from(doc.head.children)) {
    if (
      meta.tagName !== 'META' ||
      meta.getAttribute('http-equiv')?.toLowerCase() !==
        'content-security-policy'
    )
      continue
    const nonces = new Set<string>()
    for (const m of (meta.getAttribute('content') ?? '').matchAll(
      /'nonce-([^']+)'/gi
    )) {
      nonces.add(m[1])
    }
    policies.push([meta, nonces])
  }
  return policies
}

/** 脚本的 nonce 是否被位于它之前的 bundle 生效策略声明。 */
function policyNonceFor(
  script: Element,
  own: string,
  policies: BundlePolicy[]
): boolean {
  return policies.some(
    ([meta, nonces]) =>
      nonces.has(own) &&
      (meta.compareDocumentPosition(script) &
        Node.DOCUMENT_POSITION_FOLLOWING) !==
        0
  )
}

/** 给 bundle 文档内每个真实 <script> 盖宿主 nonce（自带策略的除外）。 */
export function stampScriptNonces(doc: Document, nonce: string): void {
  const policies = bundlePolicyNonces(doc)
  for (const script of Array.from(doc.querySelectorAll('script'))) {
    const own = script.getAttribute('nonce')
    if (own && policyNonceFor(script, own, policies)) continue
    script.setAttribute('nonce', nonce)
  }
}
