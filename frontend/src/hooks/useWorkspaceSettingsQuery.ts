import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import {
  getExecutorCatalog,
  getWorkspaceExecutorConfiguration,
} from '../api/executorApi'
import type { components } from '../generated/api'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useSettingStore } from '../stores/settingStore'
import { useWorkflowDefinitionQuery } from './useWorkflowDefinitionQuery'
import {
  defaultSettings,
  normalizeExecutorConfiguration,
  type HydrateSettingsInput,
} from '../stores/setting/state'
import type { WorkspaceResponse, WorkspaceSettings } from '../types'
import type { ExecutorDefinition } from '../types/executorTypes'

type WorkspaceAgentRoutesResponse =
  components['schemas']['WorkspaceAgentRoutesResponse']

export type WorkspaceAgentRouteEntry =
  components['schemas']['WorkspaceAgentRouteEntry']

/**
 * settingStore 的服务端快照；draft 字段仍留在 store，快照只经 react-query
 * 缓存共享。executorCatalog/agentRoutes 由消费组件直接从 query data 读取。
 */
export interface WorkspaceSettingsSnapshot extends HydrateSettingsInput {
  executorCatalog: ExecutorDefinition[]
  agentRoutes: WorkspaceAgentRouteEntry[]
}

/**
 * 五个并行请求组装设置快照（原 settingStore.fetchSettings 的语义）：
 * 404 整体静默（返回 null，调用方不动 store）；agentRoutes 单独降级为空；
 * settings 合并 defaultSettings；executorConfiguration 走 normalize。
 */
async function fetchWorkspaceSettingsSnapshot(
  workspaceId: string
): Promise<WorkspaceSettingsSnapshot | null> {
  try {
    const [
      workspaceResult,
      settingsResult,
      catalogResult,
      executorConfigurationResult,
      agentRoutesResult,
    ] = await Promise.all([
      api<WorkspaceResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`
      ),
      api<
        Partial<WorkspaceSettings> | { settings: Partial<WorkspaceSettings> }
      >(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`),
      getExecutorCatalog(workspaceId),
      getWorkspaceExecutorConfiguration(workspaceId),
      api<WorkspaceAgentRoutesResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/agent-routes`
      ).catch(() => null),
    ])
    const workspaceData = workspaceResult?.workspace
    const data =
      settingsResult &&
      typeof settingsResult === 'object' &&
      'settings' in settingsResult
        ? (settingsResult.settings as Partial<WorkspaceSettings>)
        : (settingsResult as Partial<WorkspaceSettings>)
    return {
      workspaceName: workspaceData?.name || '',
      workspaceDescription: workspaceData?.description || '',
      settings: { ...defaultSettings, ...data },
      executorCatalog: catalogResult?.executors ?? [],
      executorConfiguration: normalizeExecutorConfiguration(
        executorConfigurationResult
      ),
      agentRoutes: agentRoutesResult?.routes ?? [],
    }
  } catch (err) {
    const status =
      err && typeof err === 'object' && 'status' in err
        ? Number((err as { status?: unknown }).status)
        : undefined
    if (status === 404) return null
    throw err
  }
}

export function useWorkspaceSettingsQuery(
  workspaceId: string | null | undefined
) {
  return useQuery({
    queryKey: extraQueryKeys.workspaceSettings(workspaceId ?? ''),
    queryFn: () => fetchWorkspaceSettingsSnapshot(workspaceId ?? ''),
    enabled: !!workspaceId,
  })
}

/**
 * 设置页各 section 读取服务端快照的便捷入口：workflowDefinition 按 draft 的
 * workflowKey 取（与 WorkspaceMainPage 共享缓存），executorCatalog/agentRoutes
 * 取自 settings 快照；未加载时回退空值。
 */
export function useWorkspaceSettingsSnapshot() {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const workflowKey = useSettingStore((s) => s.settings.workflowKey)
  const { data: workflowDefinition } = useWorkflowDefinitionQuery(workflowKey)
  const { data: snapshot } = useWorkspaceSettingsQuery(workspaceId)
  return {
    workflowDefinition: workflowDefinition ?? null,
    executorCatalog: snapshot?.executorCatalog ?? [],
    agentRoutes: snapshot?.agentRoutes ?? [],
  }
}

/**
 * 把 settings 快照水合进 settingStore 的 draft 字段。触发条件：
 * (a) 目标 workspaceId 与 store 当前 workspaceId 不同（切换工作区，强制重置草稿）；
 * (b) 新数据到达且 store 不 dirty（后台 refetch 不得覆盖未保存的编辑）。
 * 加载失败（非 404）写 saveError，对齐原 fetchSettings 行为。
 */
export function useSettingStoreHydration(workspaceId: string | undefined) {
  const query = useWorkspaceSettingsQuery(workspaceId)
  const { data, error } = query
  useEffect(() => {
    if (!workspaceId || !data) return
    const state = useSettingStore.getState()
    if (state.workspaceId !== workspaceId || !state.isDirty) {
      state.hydrateSettings(workspaceId, data)
    }
  }, [data, workspaceId])
  useEffect(() => {
    if (!error) return
    useSettingStore.setState({
      saveError: error instanceof Error ? error.message : '加载设置失败',
    })
  }, [error])
  return query
}
