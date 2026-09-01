/**
 * 预览面板 postMessage 桥协议（issue #328）——平台对 bundle 的长期公共契约。
 *
 * 面板 bundle 运行在 `<iframe sandbox="allow-scripts">`（永不授
 * allow-same-origin）里，origin 为 opaque（"null"），因此：
 * - 宿主不能用 event.origin 鉴别消息来源，只能钉住 event.source ===
 *   iframe.contentWindow 再校验 source 标记字段；
 * - 宿主回包用 postMessage(response, '*')：目标窗口已被 contentWindow 引用
 *   钉死，'*' 只是绕开 opaque origin 的语法要求，不会投递到别的窗口。
 *
 * 桥只暴露只读能力（listArtifacts / readArtifact / getJobDetail）与主题
 * 变量；写操作（发布、改配置）永远不走桥。新增方法即公共契约变更，需同步
 * server/app/mcp_server/preview_guide.md 与本文件的测试。
 */

export const PREVIEW_PANEL_SOURCE = 'agent-legion-preview-panel'
export const PREVIEW_HOST_SOURCE = 'agent-legion-preview-host'

/** 桥目前支持的方法（只读）。 */
export type PreviewBridgeMethod =
  | 'listArtifacts'
  | 'readArtifact'
  | 'getJobDetail'

/** 面板 → 宿主：就绪信号（宿主收到后下发 init）。 */
export interface PreviewPanelReadyMessage {
  source: typeof PREVIEW_PANEL_SOURCE
  type: 'ready'
}

/** 面板 → 宿主：内容高度自适应（宿主钳制后设置 iframe 高度）。 */
export interface PreviewPanelResizeMessage {
  source: typeof PREVIEW_PANEL_SOURCE
  type: 'resize'
  height: number
}

/** 面板 → 宿主：桥方法调用。readArtifact 需要 params.name。 */
export interface PreviewPanelRequestMessage {
  source: typeof PREVIEW_PANEL_SOURCE
  type: 'request'
  id: number
  method: PreviewBridgeMethod
  params?: { name?: string }
}

export type PreviewPanelToHostMessage =
  | PreviewPanelReadyMessage
  | PreviewPanelResizeMessage
  | PreviewPanelRequestMessage

/** 宿主 → 面板：初始化（jobId + 主题变量 + 可选资源 URL）。 */
export interface PreviewHostInitMessage {
  source: typeof PREVIEW_HOST_SOURCE
  type: 'init'
  jobId: string
  theme: Record<string, string>
  /** 平台提供的可选资源（如 katexCssUrl/katexJsUrl）；面板必须能在缺失时降级。 */
  assets: Record<string, string>
}

/** 宿主 → 面板：桥方法响应（与 request 按 id 配对）。 */
export interface PreviewHostResponseMessage {
  source: typeof PREVIEW_HOST_SOURCE
  type: 'response'
  id: number
  ok: boolean
  payload?: unknown
  error?: string
}

export type PreviewHostToPanelMessage =
  | PreviewHostInitMessage
  | PreviewHostResponseMessage

const BRIDGE_METHODS: readonly string[] = [
  'listArtifacts',
  'readArtifact',
  'getJobDetail',
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** 鉴别面板 → 宿主消息：结构 + source 标记（origin 对 opaque iframe 无意义）。 */
export function isPanelToHostMessage(
  data: unknown
): data is PreviewPanelToHostMessage {
  if (!isRecord(data) || data.source !== PREVIEW_PANEL_SOURCE) return false
  switch (data.type) {
    case 'ready':
      return true
    case 'resize':
      return typeof data.height === 'number' && Number.isFinite(data.height)
    case 'request':
      return (
        typeof data.id === 'number' &&
        typeof data.method === 'string' &&
        BRIDGE_METHODS.includes(data.method)
      )
    default:
      return false
  }
}

/** 鉴别宿主 → 面板消息（bundle 侧文档与测试用同一判定）。 */
export function isHostToPanelMessage(
  data: unknown
): data is PreviewHostToPanelMessage {
  if (!isRecord(data) || data.source !== PREVIEW_HOST_SOURCE) return false
  switch (data.type) {
    case 'init':
      return typeof data.jobId === 'string' && isRecord(data.theme)
    case 'response':
      return typeof data.id === 'number' && typeof data.ok === 'boolean'
    default:
      return false
  }
}
