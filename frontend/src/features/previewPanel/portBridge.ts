/**
 * #1178 codex 复审 P1：readArtifactBytes 的 MessagePort 通道宿主侧管理。
 *
 * 窗口通道（window.postMessage）对 request 的鉴别无法闭合——sandbox iframe
 * 自导航前后 WindowProxy 同一，导航后、load 前的窗口里伪造 source 标记的
 * request 与合法 request 不可区分（且恶意文档可悬挂子资源令 load 永不触发）。
 * 修复契约：**媒体字节通道（readArtifactBytes）只走 MessagePort**——宿主建
 * MessageChannel、把 port2 随 init transfer 给**初始 srcdoc 文档**（导航即
 * 销毁旧 global，port 随之关闭；端口无法转移到导航后的文档：旧 global 销毁
 * 即关、sandbox 无 allow-popups、宿主丢弃转发来的端口）。基础方法（文本量
 * 级，泄漏面与修复前 window request 等价）保留 window 通道兼容存量面板。
 */

import {
  handleBridgeRequest,
  type BridgeResponder,
} from './bridgeRequestHandler'
import type { JobDetail } from '../../types/jobTypes'
import type { PreviewHostInitMessage } from './bridge'

/** port 通道的面板 → 宿主 request（身份由端口持有证明，无 source 标记）。 */
export interface PreviewPortRequestMessage {
  type: 'request'
  id: number
  method: string
  params?: { name?: string }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** 鉴别 port 通道的 request（无 source 标记——端口持有即身份）。 */
export function isPortRequestMessage(
  data: unknown
): data is PreviewPortRequestMessage {
  if (!isRecord(data) || data.type !== 'request') return false
  return typeof data.id === 'number' && typeof data.method === 'string'
}

/**
 * 为一次 init 下发建新 Channel：port1 装上 request 监听（收到即执行桥方法、
 * 回包走同一 port，transfer 列表透传零拷贝），port2 交给调用方随 init
 * transfer 给初始文档。每次 init（含节点状态变化的重发）都建新 Channel——
 * 面板收到新 init 应改用新 port；旧 port1 由调用方 close（见 sendInit）。
 */
export function createPortBridge(
  jobId: string,
  detail: JobDetail | undefined,
  methodGuard: (method: string) => boolean
): { port1: MessagePort; port2: MessagePort } {
  const channel = new MessageChannel()
  channel.port1.onmessage = (event: MessageEvent) => {
    const data: unknown = event.data
    if (!isPortRequestMessage(data) || !methodGuard(data.method)) return
    const respond: BridgeResponder = (id, ok, payload, error, transfer) => {
      channel.port1.postMessage(
        {
          type: 'response',
          id,
          ok,
          ...(ok ? { payload } : { error: error ?? 'unknown error' }),
        },
        transfer ?? []
      )
    }
    void handleBridgeRequest(
      data.id,
      data.method,
      data.params,
      jobId,
      detail,
      respond
    )
  }
  return { port1: channel.port1, port2: channel.port2 }
}

/** init 消息的目标与 transfer 列表（window postMessage 第三参）。 */
export function initTransferFor(
  target: Window,
  initMessage: PreviewHostInitMessage,
  port2: MessagePort
): void {
  target.postMessage(initMessage, '*', [port2])
}
