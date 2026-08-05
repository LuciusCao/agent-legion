import { create } from 'zustand'
import type { ConnectionStatus } from '../lib/realtime'

// workers 等 server state 已迁移到 react-query（queryKeys.agentWorkers()）；
// 本 store 只保留 WS 连接态等 client state。
interface ExecutorsState {
  connectionStatus: Record<string, ConnectionStatus>
  setConnectionStatus: (channel: string, status: ConnectionStatus) => void
}

export const useExecutorsStore = create<ExecutorsState>((set) => ({
  connectionStatus: {},

  setConnectionStatus: (channel, status) =>
    set((state) => ({
      connectionStatus: { ...state.connectionStatus, [channel]: status },
    })),
}))
