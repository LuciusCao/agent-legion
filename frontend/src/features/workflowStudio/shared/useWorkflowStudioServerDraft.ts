import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import type { useWorkflowStudioData } from './useWorkflowStudioData'
import { useWorkflowStudioDraftStore } from './useWorkflowStudioDraftStore'
import { isDefinitionDirty } from './workflowStudioModel'

/** 服务端草稿及其派生比较/发布动作的 hook 组合；useWorkflowStudio 负责
 * 画布、运行通道与对外视图模型。调用顺序与原组合保持一致。 */
export function useWorkflowStudioServerDraft(
  workspaceId: string | undefined,
  data: ReturnType<typeof useWorkflowStudioData>
) {
  const draft = useWorkflowStudioDraftStore(
    workspaceId,
    data.originalYaml,
    data.workflow,
    data.revision,
    data.fetchRevisionDetail
  )
  // revision 模式下 compare 继续运行，顶栏「草稿有未发布更改」持续可见。
  const compare = useWorkflowDraftCompare(
    workspaceId,
    draft.draftYaml,
    isDefinitionDirty(data.originalYaml, draft.draftYaml),
    data.loadState === 'empty'
  )
  const actions = useWorkflowStudioActions(
    workspaceId,
    draft,
    data.reload,
    compare
  )
  return { draft, compare, actions }
}
