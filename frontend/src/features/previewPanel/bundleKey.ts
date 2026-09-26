/**
 * PreviewPanelHost 挂载 key 的 bundle 指纹（codex P2 重挂语义；#615 从
 * PreviewPanelSection 提取为公共助手——曾供左栏与对话框内嵌预览两个挂载点
 * 共用，#615 方向 A 撤掉内嵌预览后只剩左栏单一挂载点；抽出的另一消费方是
 * useDraftAuthorization 的授权判定，指纹口径仍单一点维护）。
 *
 * key 含 jobId 与 bundle 内容指纹：内容变化即整树重挂 iframe——沿用同一
 * contentWindow 做 srcDoc 导航，旧文档仍在途的桥请求会由宿主把响应投递给
 * 同一个 WindowProxy，而新文档的请求编号又从 1 重新计数，旧响应可能错误
 * 地应答新文档的同编号请求；重挂使旧窗口销毁、在途响应无处可投。
 *
 * 内容指纹用**服务端 html_hash**（sha256(bundle)，见后端
 * preview_panels.bundle_hash）而非前端自算（codex P2 修复）：前端曾自算
 * 32 位多项式滚动哈希，`Aa`/`BB` 等可构造碰撞会让不同内容共享 key、
 * srcDoc 同窗导航绕过重挂保证。版本化契约里 html 与 html_hash 同对象
 * 下发：内容相同 → hash 相同（轮询不重挂），内容不同 → hash 必不同
 * （sha256 抗碰撞，必重挂）。授权判定（useDraftAuthorization）比对同一
 * html_hash 字段——内容身份单一口径，key 与授权不吃两套指纹。
 */
export function previewHostKey(jobId: string, htmlHash: string): string {
  return `${jobId}:${htmlHash}`
}
