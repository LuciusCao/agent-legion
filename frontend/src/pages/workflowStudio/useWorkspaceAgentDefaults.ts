import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../../api'
import type { AgentDefaults, WorkspaceSettingsResponse } from '../../types'

/**
 * Workspace 级 Agent 默认执行配置（Settings「Agent 默认配置」）。
 * Studio 节点编辑器「继承默认」提示的唯一来源：executor catalog 是全局
 * 只读投影，已不再携带 provider/model/thinking（agent 配置治理 phase 3）。
 */
export function useWorkspaceAgentDefaults(): AgentDefaults | undefined {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [defaults, setDefaults] = useState<AgentDefaults | undefined>(undefined)
  useEffect(() => {
    if (!workspaceId) return
    let cancelled = false
    api<WorkspaceSettingsResponse>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/settings`
    )
      .then((payload) => {
        if (cancelled) return
        const settings = payload.settings as { agentDefaults?: AgentDefaults }
        setDefaults(settings.agentDefaults ?? {})
      })
      .catch(() => {
        if (!cancelled) setDefaults(undefined)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId])
  return workspaceId ? defaults : undefined
}
