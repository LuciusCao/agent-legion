import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../../types'
import type { DraftSaveFlushResult, DraftSaveState } from './draftSaveTypes'
import { useServerDraftApply } from './useServerDraftApply'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'
import { useWorkflowDraftQuery } from './useWorkflowDraftQuery'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

/** 草稿组合：useWorkflowStudioDraft（内存草稿）+ 服务端草稿查询/应用 +
 * 自动持久化。useWorkflowStudio 只与本 hook 对接，保持各自文件的体积预算。 */
export type DraftStoreControls = {
  draftSave: DraftSaveState
  /** #429 收尾 P2-1：flush 的返回值携带本次落盘的终态（ok=false 即失败），
   * 发布确认的守卫读它——不读 React useState 快照的 draftSave.status。 */
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
  const draftQuery = useWorkflowDraftQuery(workspaceId)
  const draft = useWorkflowStudioDraft(
    workspaceId,
    originalYaml,
    activeWorkflow,
    activeRevision,
    fetchRevisionDetail
  )
  const serverDraft = draftQuery.data
  const { setDraftYaml, consumeConflict } = useServerDraftApply(
    workspaceId,
    originalYaml,
    serverDraft === undefined ? undefined : serverDraft.definition_yaml,
    serverDraft === undefined ? undefined : serverDraft.updated_at,
    draft.setDraftYaml,
    /* kimi review P1-1：own-save 回显判定需要当前画布内容。 */
    draft.draftYaml
  )
  // #633 codex review P1-2：服务端草稿前进且画布采用了它（用户无本地
  // 编辑）时，保存层同步 hydrate——lastPersistedAt 推进到服务端真值，
  // 后续 PUT 以新基线竞争而不是用过期时间戳 409。
  const draftSave = useWorkflowDraftPersistence(
    workspaceId,
    draft.draftYaml,
    originalYaml,
    serverDraft,
    draftQuery.isError,
    consumeConflict
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
  return {
    ...draft,
    setDraftYaml,
    useViewedRevisionAsDraft,
    draftSave: draftSave.state,
    flushDraftSave: draftSave.flushNow,
    /* kimi review P1-2：冲突出口——采用服务端版本经 touched-aware setter
       写画布；keep-mine 继续保存。 */
    adoptServerDraft: (yaml: string, at: string | null) =>
      draftSave.adoptServerDraft(yaml, at, setDraftYaml),
    resolveConflict: draftSave.resolveConflict,
  }
}
