import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { useServerDraftApply } from './useServerDraftApply'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'

export type ServerDraftPersistenceInput = {
  workspaceId: string | undefined
  originalYaml: string
  draftYaml: string
  setDraftYamlState: (value: string) => void
  serverDraft: WorkflowDraftStoreResponse | undefined
  serverDraftLoadError: boolean
}

/** 组合画布重应用与保存水合：apply 必须先注册 effect，persistence 随后
 * 消费冲突并推进 CAS 基线。采用服务端版本仍经 touched-aware setter 写
 * 画布；keep-mine 由 persistence 读取最新画布内容。 */
export function useServerDraftPersistence(input: ServerDraftPersistenceInput) {
  const { setDraftYaml, consumeConflict } = useServerDraftApply({
    workspaceId: input.workspaceId,
    originalYaml: input.originalYaml,
    serverDraftYaml: input.serverDraft?.definition_yaml,
    serverDraftUpdatedAt: input.serverDraft?.updated_at,
    setDraftYamlState: input.setDraftYamlState,
    canvasYaml: input.draftYaml,
    serverDraftHash: input.serverDraft?.definition_hash,
    serverDraftGeneration: input.serverDraft?.generation,
  })
  const draftSave = useWorkflowDraftPersistence(
    input.workspaceId,
    input.draftYaml,
    input.originalYaml,
    input.serverDraft,
    input.serverDraftLoadError,
    consumeConflict
  )
  const adoptServerDraft = (
    yaml: string,
    at: string | null,
    hash?: string | null,
    generation?: number | null
  ) => draftSave.adoptServerDraft(yaml, at, setDraftYaml, hash, generation)
  return { setDraftYaml, draftSave, adoptServerDraft }
}
