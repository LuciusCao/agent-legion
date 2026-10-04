/**
 * #914/#885：SSE 静默挂起看门狗。
 *
 * 连接被静默挂起（dev 代理背后的后端已死、NAT/反代/企业代理静默丢弃长
 * 连接）时浏览器永远收不到 EventSource 的 `error`。服务端心跳因此是带数据
 * 的具名事件（`event: heartbeat`，data 带 `interval_ms`，单一来源为后端
 * `server/app/events/sse.py` 的 HEARTBEAT_SECONDS）：记录最后一次收到任何
 * 事件（含心跳）的时刻，连续超过「间隔 × SSE_STALL_TIMEOUT_MULTIPLIER」
 * 无数据即回调 onStall，由 realtime 层按 `error` 同一路径重连。
 *
 * 未收到过可解析的心跳前不武装（不对不发心跳的服务端误判）；学到的间隔
 * 跨重连保留，重连后「打开即挂起」同样能被识别。
 */
export const SSE_HEARTBEAT_EVENT = 'heartbeat'
export const SSE_STALL_TIMEOUT_MULTIPLIER = 2.5

/** 从心跳 data 解析服务端约定的间隔；无法解析返回 null。 */
function parseHeartbeatIntervalMs(data: unknown): number | null {
  if (typeof data !== 'string') return null
  try {
    const value = (JSON.parse(data) as { interval_ms?: unknown }).interval_ms
    return typeof value === 'number' && Number.isFinite(value) && value > 0
      ? value
      : null
  } catch {
    return null
  }
}

export interface SseStallWatchdog {
  /** 新连接打开：绑定本连接的 onStall 并以此刻为最后活动时间。 */
  start: (onStall: () => void) => void
  /** 收到任意事件。 */
  activity: () => void
  /** 收到心跳事件（data 为原始字符串）。 */
  heartbeat: (data: unknown) => void
  /** 连接结束（出错/关闭/判挂起）：解绑并清计时器。 */
  stop: () => void
}

export function createSseStallWatchdog(): SseStallWatchdog {
  let intervalMs: number | null = null
  let lastEventAt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let onStall: (() => void) | null = null

  const clear = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  const arm = () => {
    clear()
    if (intervalMs === null || onStall === null) return
    const timeoutMs = intervalMs * SSE_STALL_TIMEOUT_MULTIPLIER
    // 计时器只按 lastEventAt 复核、到点未过期就顺延，事件到达不必重置它。
    timer = setTimeout(
      () => {
        timer = null
        if (Date.now() - lastEventAt < timeoutMs) {
          arm()
          return
        }
        const stalled = onStall
        onStall = null
        stalled?.()
      },
      Math.max(lastEventAt + timeoutMs - Date.now(), 0)
    )
  }

  const activity = () => {
    if (onStall === null) return
    lastEventAt = Date.now()
    if (timer === null) arm()
  }

  return {
    start: (callback) => {
      onStall = callback
      lastEventAt = Date.now()
      arm()
    },
    activity,
    heartbeat: (data) => {
      const interval = parseHeartbeatIntervalMs(data)
      if (interval !== null && interval !== intervalMs) {
        intervalMs = interval
        clear()
      }
      activity()
    },
    stop: () => {
      onStall = null
      clear()
    },
  }
}
