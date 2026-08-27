import { useCallback, useEffect, useRef } from 'react'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionDetailResponse,
  WorkflowRevisionSummary,
} from '../../types'
import {
  useWorkflowDraftPersistence,
  useWorkflowDraftQuery,
} from './useWorkflowDraftPersistence'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

/** 服务端草稿的一次性应用 + 用户编辑追踪。返回包装后的 setDraftYaml：
 * 只有经它（用户编辑/聊天应用/重置/采用历史版本）的写入才算「用户碰过」；
 * useDraftBaselineSync 的内部 reset 不算。应用规则（与
 * useWorkflowDraftPersistence 的 hydrated 规则配套）：草稿查询到达（非
 * undefined）且基线已知后，草稿未被碰过就用服务端草稿替换基线（服务端
 * 草稿 ≠ 基线即 dirty）。本 effect 在 useWorkflowStudioDraft 的基线同步
 * 之后注册，同一 commit 内基线首次 reset 先跑、本 effect 后跑，无论两个
 * 查询谁先返回，最终都是服务端草稿胜出。 */
function useServerDraftApply(
  workspaceId: string | undefined,
  originalYaml: string,
  serverDraftYaml: string | null | undefined,
  setDraftYamlState: (value: string) => void
): (value: string) => void {
  const userTouchedRef = useRef(false)
  const appliedRef = useRef(false)
  useEffect(() => {
    appliedRef.current = false
    userTouchedRef.current = false
  }, [workspaceId])
  useEffect(() => {
    if (appliedRef.current || serverDraftYaml === undefined || !originalYaml) {
      return
    }
    appliedRef.current = true
    if (serverDraftYaml !== null && !userTouchedRef.current) {
      setDraftYamlState(serverDraftYaml)
    }
  }, [serverDraftYaml, originalYaml, setDraftYamlState])
  return useCallback(
    (value: string) => {
      userTouchedRef.current = true
      setDraftYamlState(value)
    },
    [setDraftYamlState]
  )
}

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
    draftQuery.data
  )
  return { ...draft, setDraftYaml, useViewedRevisionAsDraft, draftSave }
}
