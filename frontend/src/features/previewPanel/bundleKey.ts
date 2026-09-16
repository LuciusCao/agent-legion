/**
 * PreviewPanelHost 挂载 key 的 bundle 指纹（codex P2 重挂语义；#615 从
 * PreviewPanelSection 提取为公共助手——左栏与「定制预览」对话框内嵌预览
 * 两个挂载点必须共用同一实现，不允许两套指纹漂移）。
 *
 * key 含 jobId 与 bundle 内容：内容变化即整树重挂 iframe——沿用同一
 * contentWindow 做 srcDoc 导航，旧文档仍在途的桥请求会由宿主把响应投递给
 * 同一个 WindowProxy，而新文档的请求编号又从 1 重新计数，旧响应可能错误
 * 地应答新文档的同编号请求；重挂使旧窗口销毁、在途响应无处可投。
 *
 * 授权判定**不用**它——比对服务端 html_hash（sha256），见
 * useDraftAuthorization（同理：避免 key 指纹与授权指纹两套口径漂移）。
 */
export function previewHostKey(jobId: string, html: string): string {
  let hash = 0
  for (let i = 0; i < html.length; i++) {
    hash = (Math.imul(hash, 31) + html.charCodeAt(i)) | 0
  }
  return `${jobId}:${(hash >>> 0).toString(36)}`
}
