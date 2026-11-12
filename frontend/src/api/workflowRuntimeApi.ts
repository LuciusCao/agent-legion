import { api } from './core'
import type { components } from '../generated/api'

type SaveRequest = components['schemas']['WorkflowRuntimeSaveRequest']
type SaveResponse = components['schemas']['WorkflowRuntimeSaveResponse']
type VersionsResponse = components['schemas']['WorkflowRuntimeVersionsResponse']

/** 运行通道（M47/M48）：runtime 字段即改即存——原地生效于 active revision
 * 并追加配置版本行，不产生新 revision。definition_yaml 的结构切片必须与
 * active 一致（结构改动走草稿发布通道，否则 409）。 */
export async function saveWorkflowRuntimeConfig(
  workspaceId: string,
  definitionYaml: string
): Promise<SaveResponse> {
  const body: SaveRequest = { definition_yaml: definitionYaml }
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-runtime`,
    { method: 'PATCH', body: JSON.stringify(body) }
  )
}

export async function fetchWorkflowRuntimeVersions(
  workspaceId: string
): Promise<VersionsResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-runtime/versions`
  )
}

/** 回滚 = 把目标版本的 runtime 切片应用到当前结构后追加为新版本（历史不篡改）。 */
export async function rollbackWorkflowRuntimeConfig(
  workspaceId: string,
  version: number
): Promise<SaveResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-runtime/rollback`,
    { method: 'POST', body: JSON.stringify({ version }) }
  )
}
