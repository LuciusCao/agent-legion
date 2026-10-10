/**
 * 已接受字节桥端口的宿主侧服务（#1178；从 portBridge.ts 拆出——预算纪律：
 * 接受器（能力发放的首次-only 鉴别）留在 portBridge，这里只管 serving）。
 *
 * 并发护栏（#1178 codex 复审 P1，第 6 轮）：单次读取的 512 MiB 上限不约束
 * 并发总量——面板并发请求（多媒体元素 / init 重发触发的叠加重取）会把
 * 多份近上限读取叠加冻结标签页。每个 port 上的字节读取串行成一条
 * promise 链：同一时刻至多一次在途读取（单次峰值含流式累积与合并缓冲
 * ≈ 2×512 MiB，由 artifactByteStream 的形态决定，不再叠加）。
 */
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
 * 给已接受的端口装上宿主侧服务：字节读取串行队列 + readArtifactBytes
 * 执行（基础三法走窗口通道，这里明确拒绝并引导，见 preview_guide.md）。
 */
export function serveBytePort(
  port: MessagePort,
  deps: { jobId: string; getDetail: () => JobDetail | undefined }
): void {
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
  // 串行队列（见文件头并发护栏）：新请求排在上一次读取完成之后——
  // handleBridgeRequest 内部捕获错误走错误响应通道、永不 reject，链条
  // 不会因单次失败中断。
  let queue: Promise<void> = Promise.resolve()
  port.onmessage = (event: MessageEvent) => {
    const data: unknown = event.data
    if (!isPortRequestMessage(data)) return
    if (data.method !== 'readArtifactBytes') {
      respond(data.id, false, undefined, `${data.method} ${NOT_SERVED_HERE}`)
      return
    }
    queue = queue.then(() =>
      handleBridgeRequest(
        data.id,
        data.method,
        data.params,
        deps.jobId,
        deps.getDetail(),
        respond
      )
    )
  }
  port.start?.()
}
