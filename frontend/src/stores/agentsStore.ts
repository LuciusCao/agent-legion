import { create } from 'zustand'
import { createRealtimeChannel, type RealtimeChannel } from '../lib/realtime'
import { parseAgentsWsMessage, upsertAgent } from '../lib/agentsWsMessages'
import { useConnectionStatusStore } from './connectionStatusStore'
import type { AgentStatus } from '../types'
import {
  createWorkerStatusActions,
  type WorkerStatusRead,
} from './workerSchedulingState'

/** #961：workspace 暂停位不再存在本 store——唯一数据源是 React Query 缓存
 * （hooks/useWorkerPausedStatus）；这里只保留请求排序动作，避免 zustand 与
 * RQ 双源分叉（拉取失败时 store 默认值曾把运行中显示成「已暂停」）。 */
export interface AgentsState {
  agents: AgentStatus[]
  connectAgentsWs: () => () => void
  fetchWorkerStatus: (workspaceId: string) => Promise<WorkerStatusRead>
  /** 返回服务端确认后的 paused；调用方负责写回 RQ 缓存。 */
  setWorkerPaused: (paused: boolean, workspaceId: string) => Promise<boolean>
}

let agentsChannel: RealtimeChannel | null = null

export const useAgentsStore = create<AgentsState>((set) => ({
  agents: [],

  connectAgentsWs: () => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    agentsChannel?.close()
    agentsChannel = createRealtimeChannel({
      url: `${protocol}//${location.host}/api/agents`,
      protocol: 'ws',
      onStatus: (status) => {
        useConnectionStatusStore
          .getState()
          .setConnectionStatus('agents', status)
      },
      onEvent: (_type, data) => {
        try {
          const message = parseAgentsWsMessage(data)
          if (message === null) return
          if (Array.isArray(message)) {
            set({ agents: message })
            return
          }
          // Destructure: a direct dot-access on the envelope's `agent`
          // field trips the WorkflowNode governance ratchet.
          const { agent: incoming } = message
          set((state) => ({ agents: upsertAgent(state.agents, incoming) }))
        } catch {
          // ignore malformed messages
        }
      },
    })
    return () => {
      agentsChannel?.close()
      agentsChannel = null
    }
  },

  ...createWorkerStatusActions(),
}))
