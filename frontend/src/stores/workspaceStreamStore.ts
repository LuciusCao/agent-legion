import { create } from 'zustand'
import type { ConnectionStatus } from '../lib/realtime'

/**
 * #720：workspace 实时流（SSE，job 进度主通道）的连接态，供 UI 提示
 * 「连接中断，重连中」。与 AgentConnectionDot 同一模式：realtime 层的
 * onStatus → 本 store → 订阅组件只在非健康态渲染。
 *
 * realtime 层每次建连前发 `connecting`、成功发 `open`、主动关闭发
 * `closed`（断线本身不发事件，退避到点后再发 `connecting`）。因此：
 * - 打开过之后再出现 `connecting` = 断线重连，记下 `staleSince`（进度
 *   从此刻起可能已过时）；
 * - 从未打开过而 `connecting` 出现第二次 = 首连失败、正在重试；
 * - `closed` 只在页面卸载/切换 workspace 时出现，直接复位。
 *
 * #918：用户可关闭提示条（`dismissed`）。关闭态只在内存里（不落任何
 * storage），刷新页面即复位；连接恢复 `open` 时清零，下次断线照常提示。
 * 「未建立」「中断重连」两种变体共用这一个关闭态；看门狗判定的断线（#914）
 * 与 error 断线走同一 status 流，因此也走同一关闭/复位逻辑。
 */
interface WorkspaceStreamState {
  workspaceId: string | null
  status: ConnectionStatus | null
  /** 本次挂载内是否成功打开过。 */
  everOpened: boolean
  /** 连续未成功的建连次数（open 时清零）。 */
  attempts: number
  /** 断线时刻（ms epoch）；null = 实时数据未中断。 */
  staleSince: number | null
  /** 用户已关闭本次断线周期的提示（#918）。 */
  dismissed: boolean
  setStatus: (workspaceId: string, status: ConnectionStatus) => void
  dismiss: (workspaceId: string) => void
}

const IDLE = {
  workspaceId: null,
  status: null,
  everOpened: false,
  attempts: 0,
  staleSince: null,
  dismissed: false,
} as const

export const useWorkspaceStreamStore = create<WorkspaceStreamState>(
  (set, get) => ({
    ...IDLE,
    setStatus: (workspaceId, status) => {
      const current = get()
      if (status === 'closed') {
        // 迟到的旧 workspace 关闭事件不能清掉新 workspace 的状态。
        if (current.workspaceId === workspaceId) set({ ...IDLE })
        return
      }
      const base =
        current.workspaceId === workspaceId ? current : { ...IDLE, workspaceId }
      if (status === 'open') {
        set({
          workspaceId,
          status,
          everOpened: true,
          attempts: 0,
          staleSince: null,
          dismissed: false,
        })
        return
      }
      set({
        workspaceId,
        status,
        everOpened: base.everOpened,
        attempts: base.attempts + 1,
        staleSince:
          base.everOpened && base.staleSince === null
            ? Date.now()
            : base.staleSince,
        dismissed: base.dismissed,
      })
    },
    dismiss: (workspaceId) => {
      if (get().workspaceId === workspaceId) set({ dismissed: true })
    },
  })
)

export type WorkspaceStreamHealth =
  | { kind: 'live' }
  | { kind: 'reconnecting'; staleSince: number }
  | { kind: 'unreachable' }

/** 归纳为 UI 可直接渲染的健康态；首连进行中（尚无失败）视为 live 不打扰。 */
export function selectWorkspaceStreamHealth(
  state: Pick<
    WorkspaceStreamState,
    'workspaceId' | 'status' | 'everOpened' | 'attempts' | 'staleSince'
  >,
  workspaceId: string
): WorkspaceStreamHealth {
  if (state.workspaceId !== workspaceId || state.status !== 'connecting') {
    return { kind: 'live' }
  }
  if (state.staleSince !== null) {
    return { kind: 'reconnecting', staleSince: state.staleSince }
  }
  if (!state.everOpened && state.attempts >= 2) return { kind: 'unreachable' }
  return { kind: 'live' }
}
