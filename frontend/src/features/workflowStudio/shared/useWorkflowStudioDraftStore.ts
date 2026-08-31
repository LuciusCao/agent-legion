import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../../types'
import { useServerDraftApply } from './useServerDraftApply'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'
import { useWorkflowDraftQuery } from './useWorkflowDraftQuery'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

/** 草稿组合：useWorkflowStudioDraft（内存草稿）+ 服务端草稿查询/应用 +
 * 自动持久化。useWorkflowStudio 只与本 hook 对接，保持各自文件的体积预算。 */
export function useWorkflowStudioDraftStore(
  workspaceId: string | undefined,
  originalYaml: string,
  activeWorkflow: WorkflowDefinitionRecord | null,
  activeRevision: WorkflowRevisionSummary | null,
  fetchRevisionDetail: (
    revisionId: string
  ) => Promise<WorkflowRevisionDetailResponse>
) {
  const draftQuery = useWorkflowDraftQuery(workspaceId)
  const draft = useWorkflowStudioDraft(
    workspaceId,
    originalYaml,
    activeWorkflow,
    activeRevision,
    fetchRevisionDetail
  )
  const setDraftYaml = useServerDraftApply(
    workspaceId,
    originalYaml,
    draftQuery.data === undefined ? undefined : draftQuery.data.definition_yaml,
    draft.setDraftYaml
  )
  // 采用历史版本也算「用户碰过」：useViewedRevisionAsDraft 内部闭包的是
  // 原始 setter，先经 touched-aware setter 写入同一值标记 touched，否则
  // 迟到的服务端草稿会把刚采用的内容覆盖掉（revision 模式下
  // definitionYaml 即被查看版本的 YAML）。
  const useViewedRevisionAsDraft = () => {
    if (draft.viewMode === 'revision' && draft.definitionYaml) {
      setDraftYaml(draft.definitionYaml)
    }
    draft.useViewedRevisionAsDraft()
  }
  const draftSave = useWorkflowDraftPersistence(
    workspaceId,
    draft.draftYaml,
    originalYaml,
    draftQuery.data,
    draftQuery.isError
  )
  return {
    ...draft,
    setDraftYaml,
    useViewedRevisionAsDraft,
    draftSave: draftSave.state,
    flushDraftSave: draftSave.flushNow,
  }
}
