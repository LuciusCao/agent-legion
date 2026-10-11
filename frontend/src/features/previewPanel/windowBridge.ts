/**
 * 窗口通道的宿主侧消息处理（#1178 codex 复审 P1 后的双通道分工）。
 *
 * 窗口通道（`window.parent.postMessage`，source 标记）承载 ready /
 * csp-violation / resize 与基础方法（listArtifacts / readArtifact /
 * getJobDetail——文本量级，泄漏面与修复前的 window request 等价，兼容
 * 存量面板）。窗口通道对 request 的鉴别不可闭合（自导航前后 WindowProxy
 * 同一）——高危方法 readArtifactBytes 在此通道**恒拒**并引导面板改用
 * 注入 bootstrap 暴露的 `window.__agentLegionPreviewBytes`（其 port 由
 * 初始文档自建上交，见 byteBridgeBootstrap.ts / portBridge.ts）。ready
 * 的 init 回包用事件携带窗口（不依赖任何登记时点——面板脚本先于 load
 * 执行，ready 可能早于 load 到达；带非阻塞子资源的文档 load 晚于脚本，
 * 登记式判定会丢 ready 导致面板永久空白，评审 P1-2 回归）。
 */
import { isPanelToHostMessage, PREVIEW_HOST_SOURCE } from './bridge'
import { handleBridgeRequest } from './bridgeRequestHandler'
import type { JobDetail } from '../../types/jobTypes'

export interface WindowBridgeHandlers {
  onReady: () => void
  onCspViolation: () => void
  onResize: (height: number) => void
}

export function createWindowMessageListener(
  handlers: WindowBridgeHandlers,
  deps: {
    jobId: string
    detail: JobDetail | undefined
  }
) {
  function onMessage(event: MessageEvent) {
    const source = event.source as Window | null
    if (!source) return
    const data: unknown = event.data
    if (!isPanelToHostMessage(data)) return
    if (data.type === 'ready') {
      handlers.onReady()
      return
    }
    if (data.type === 'csp-violation') {
      handlers.onCspViolation()
      return
    }
    if (data.type === 'resize') {
      handlers.onResize(data.height)
      return
    }
    // byte-port-offer 由 Host 的 acceptor 先行消费；漏到这里说明无 acceptor
    // （或顺序变化），不进入 request 分支。
    if (data.type !== 'request') return
    // request（基础方法；窗口来源由调用方在 listener 外层判定——本函数
    // 只处理已通过 event.source === contentWindow 的消息）：
    const respondTo = (
      id: number,
      ok: boolean,
      payload?: unknown,
      error?: string,
      transfer?: Transferable[]
    ) => {
      source.postMessage(
        {
          source: PREVIEW_HOST_SOURCE,
          type: 'response',
          id,
          ok,
          ...(ok ? { payload } : { error: error ?? 'unknown error' }),
        },
        '*',
        transfer
      )
    }
    if (data.method === 'readArtifactBytes') {
      // 高危方法不经窗口通道（#1178 P1）：明确错误引导面板改用注入全局。
      respondTo(
        data.id,
        false,
        undefined,
        'readArtifactBytes requires the host-injected byte bridge' +
          ' (window.__agentLegionPreviewBytes, see preview_guide.md);' +
          ' window-channel requests are rejected'
      )
      return
    }
    void handleBridgeRequest(
      data.id,
      data.method,
      data.params,
      deps.jobId,
      deps.detail,
      respondTo
    )
  }
  return onMessage
}
