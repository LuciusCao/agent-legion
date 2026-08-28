import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react'
import { isDefinitionDirty } from './workflowStudioModel'
import {
  createDraftViewState,
  type WorkflowStudioViewState,
} from './workflowStudioViewState'

/** 草稿与基线（active revision YAML）的同步：基线变化时通常把草稿 reset
 * 到新基线；但基线在外部变化（他人/他 tab 发布、窗口聚焦重取）且用户有
 * 未发布编辑时保留草稿而不是静默覆盖，并打 hasPreservedDraft 标记（命令
 * 栏 chip 提示）。自己 publish 成功后草稿与新基线一致，仍走常规 reset。
 * 首次运行是初始化装载，不是外部变更，必须跟随基线。preserved 标记在草稿
 * 回到与基线一致后自动消退（chip 不留滞）。 */
export function useDraftBaselineSync(
  originalYaml: string,
  activeRevisionId: string | null | undefined,
  draftYaml: string,
  setDraftYaml: (value: string) => void,
  setViewState: Dispatch<SetStateAction<WorkflowStudioViewState>>,
  clearRevisionLoadError: () => void,
  hasPreservedDraft: boolean
) {
  const draftYamlRef = useRef(draftYaml)
  useEffect(() => {
    draftYamlRef.current = draftYaml
  }, [draftYaml])
  const originalYamlRef = useRef(originalYaml)
  const baselineLoadedRef = useRef(false)
  useEffect(() => {
    const previousOriginal = originalYamlRef.current
    originalYamlRef.current = originalYaml
    const currentDraft = draftYamlRef.current
    const firstRun = !baselineLoadedRef.current
    baselineLoadedRef.current = true
    const preserveDirtyDraft =
      !firstRun &&
      isDefinitionDirty(previousOriginal, currentDraft) &&
      isDefinitionDirty(originalYaml, currentDraft)
    if (!preserveDirtyDraft) {
      setDraftYaml(originalYaml)
    }
    setViewState({
      ...createDraftViewState(activeRevisionId ?? null),
      hasPreservedDraft: preserveDirtyDraft,
    })
    clearRevisionLoadError()
  }, [
    originalYaml,
    activeRevisionId,
    clearRevisionLoadError,
    setDraftYaml,
    setViewState,
  ])

  useEffect(() => {
    if (hasPreservedDraft && !isDefinitionDirty(originalYaml, draftYaml)) {
      setViewState((current) => ({ ...current, hasPreservedDraft: false }))
    }
  }, [originalYaml, draftYaml, hasPreservedDraft, setViewState])
}
