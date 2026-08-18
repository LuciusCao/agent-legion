import { fetchActiveWorkflowRevision } from '../../api'
import type {
  ActiveWorkflowRevisionResponse,
  WorkspaceRecord,
} from '../../types'

/** 空态引导文案（loadState==='empty' 时由编辑区顶部展示）。 */
export const EMPTY_WORKFLOW_GUIDANCE =
  '该 workspace 还没有已发布的 workflow：从模板草稿开始，编辑 YAML 后对比并发布即可创建 v1。'

/** Active revision, or null when the workspace has never published one (404). */
export async function fetchActiveRevisionOrNull(
  workspaceId: string
): Promise<ActiveWorkflowRevisionResponse | null> {
  try {
    return await fetchActiveWorkflowRevision(workspaceId)
  } catch (error) {
    if ((error as { status?: number } | null)?.status === 404) return null
    throw error
  }
}

/**
 * Studio 空态模板：workspace 存在但从未发布 revision 时，注入最小单节点
 * YAML 作为草稿起点（key 钉在 workspace default_workflow_key，publish
 * 校验要求两者一致）。workspace 本身不存在时返回 null（按加载失败处理）。
 */
export function resolveEmptyTemplateYaml(
  data: { active: unknown; workspaces: WorkspaceRecord[] } | undefined,
  workspaceId: string | undefined
): string | null {
  if (!data || data.active !== null || !workspaceId) return null
  const workspace = data.workspaces.find((w) => w.id === workspaceId)
  if (!workspace) return null
  const key = workspace.default_workflow_key
  return `key: ${key}\nlabel: ${key}\nnodes:\n  start:\n    capability: start\n`
}
