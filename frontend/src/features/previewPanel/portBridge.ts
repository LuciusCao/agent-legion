/**
 * #1178 codex 复审 P1（第 4 轮）的根因修复：字节桥能力的发放与鉴别。
 *
 * 窗口通道无法区分「初始 srcdoc 文档」与「面板自导航后的文档」
 * （WindowProxy 跨导航同一、opaque origin 下 event.origin 恒 "null"、
 * load 事件有导航后时序窗且可被悬挂子资源无限拖延）。因此能力发放反转
 * 为「初始文档自证」：宿主注入的 bootstrap（byteBridgeBootstrap.ts，
 * 钉在 bundle head 第一个脚本）在解析期自建 MessageChannel——port2 闭
 * 包持有（面板只拿到 readArtifactBytes 函数，端口本体不可取出/转交），
 * port1 经 byte-port-offer 消息上交。本模块的 acceptor **每个 iframe
 * 挂载只接受第一次上交**：bootstrap 先于 bundle 任何代码执行，其 offer
 * 先于任何导航后文档可能发出的消息入队（排序即鉴别）；其后的上交（含
 * 攻击者伪造）一律拒绝并关闭。面板自导航销毁旧 global，闭包 port2 随之
 * 失效——能力绑定初始文档存活期，宿主永不重新发放（init 重发只带数据）。
 */
import { BYTE_PORT_OFFER_TYPE, isPanelToHostMessage } from './bridge'
import {
  handleBridgeRequest,
  type BridgeResponder,
} from './bridgeRequestHandler'
import type { JobDetail } from '../../types/jobTypes'

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

/** port 通道非 readArtifactBytes 方法的拒绝语（基础三法走窗口通道）。 */
const NOT_SERVED_HERE = 'is not served on the byte bridge port'

/**
 * 字节桥 port 的接受器（每 iframe 挂载一个，见 PreviewPanelHost）：只接受
 * 第一次 byte-port-offer 上交，其后（含面板自导航后外部文档的伪造）一律
 * 拒绝并关闭上交端口；close() 后永久拒收（卸载 / 检出自导航时调用）。
 */
export interface BytePortAcceptor {
  /** 是 byte-port-offer 消息即消费（接受或拒绝），返回是否已消费。 */
  handleMessage(event: MessageEvent): boolean
  close(): void
}

export function createBytePortAcceptor(deps: {
  jobId: string
  getDetail: () => JobDetail | undefined
}): BytePortAcceptor {
  let accepted: MessagePort | null = null
  let closed = false

  function attach(port: MessagePort): void {
    const respond: BridgeResponder = (id, ok, payload, error, transfer) =>
      port.postMessage(
        {
          type: 'response',
          id,
          ok,
          ...(ok ? { payload } : { error: error ?? 'unknown error' }),
        },
        transfer ?? []
      )
    port.onmessage = (event: MessageEvent) => {
      const data: unknown = event.data
      if (!isPortRequestMessage(data)) return
      // 字节桥只服务 readArtifactBytes；基础三法走窗口通道（面板契约）。
      if (data.method !== 'readArtifactBytes') {
        respond(data.id, false, undefined, `${data.method} ${NOT_SERVED_HERE}`)
        return
      }
      void handleBridgeRequest(
        data.id,
        data.method,
        data.params,
        deps.jobId,
        deps.getDetail(),
        respond
      )
    }
    port.start?.()
  }

  return {
    handleMessage(event) {
      const data: unknown = event.data
      if (!isPanelToHostMessage(data) || data.type !== BYTE_PORT_OFFER_TYPE) {
        return false
      }
      const [offered, ...extra] = event.ports
      extra.forEach((port) => port.close())
      // 非首次上交（含导航后伪造）、无端口或已关闭：拒绝——能力永不重发。
      if (closed || accepted !== null || !offered) {
        offered?.close()
        return true
      }
      accepted = offered
      attach(offered)
      return true
    },
    close() {
      closed = true
      accepted?.close()
      accepted = null
    },
  }
}
