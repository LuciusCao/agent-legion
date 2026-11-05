import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../../types'
import { useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import type { DraftSaveFlushResult, DraftSaveState } from './draftSaveTypes'
import { useServerDraftPersistence } from './useServerDraftPersistence'
import { useWorkflowDraftQuery } from './useWorkflowDraftQuery'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

/** 草稿组合：useWorkflowStudioDraft（内存草稿）+ 服务端草稿查询/应用 +
 * 自动持久化。useWorkflowStudio 只与本 hook 对接，保持各自文件的体积预算。 */
export type DraftStoreControls = {
  draftSave: DraftSaveState
  /** flush 的返回值携带本次落盘终态，发布确认守卫不读 React 状态快照。 */
  flushDraftSave: (keepalive?: boolean) => Promise<DraftSaveFlushResult>
}

export function useWorkflowStudioDraftStore(
  workspaceId: string | undefined,
  originalYaml: string,
  activeWorkflow: WorkflowDefinitionRecord | null,
  activeRevision: WorkflowRevisionSummary | null,
  fetchRevisionDetail: (
    revisionId: string
  ) => Promise<WorkflowRevisionDetailResponse>
) {
  const queryClient = useQueryClient()
  const draftQuery = useWorkflowDraftQuery(workspaceId)
  const draft = useWorkflowStudioDraft(
    workspaceId,
    originalYaml,
    activeWorkflow,
    activeRevision,
    fetchRevisionDetail
  )
  const { setDraftYaml, draftSave, adoptServerDraft } =
    useServerDraftPersistence({
      workspaceId,
      originalYaml,
      draftYaml: draft.draftYaml,
      setDraftYamlState: draft.setDraftYaml,
      serverDraft: draftQuery.data,
      serverDraftLoadError: draftQuery.isError,
    })
  // 采用历史版本也算「用户碰过」：先经 touched-aware setter 写入同一值，
  // 迟到的服务端草稿才不会覆盖刚采用的内容。
  const useViewedRevisionAsDraft = () => {
    if (draft.viewMode === 'revision' && draft.definitionYaml) {
      setDraftYaml(draft.definitionYaml)
    }
    draft.useViewedRevisionAsDraft()
  }
  return {
    ...draft,
    setDraftYaml,
    useViewedRevisionAsDraft,
    draftSave: draftSave.state,
    flushDraftSave: draftSave.flushNow,
    adoptServerDraft,
    resolveConflict: draftSave.resolveConflict,
    /* 发布成功后失效服务端草稿查询：空态响应把保存控制器重新水合到
       新 canonical 基线，尾随 schedule 不会 PUT 重建刚删的草稿行。 */
    invalidateServerDraft: () =>
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.workflowStudioDraft(workspaceId ?? ''),
      }),
  }
}
