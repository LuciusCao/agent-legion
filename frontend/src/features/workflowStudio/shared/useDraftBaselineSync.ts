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
 * 回到与基线一致后自动消退（chip 不留滞）。
 * #666：发布存库的 revision YAML 是 canonical 重建（丢注释、重排 key、补
 * 默认值），发布后 reload 拉回的新基线与草稿原文纯文本不等，preserve 判定
 * 会误判为外部变更让 dirty 永真。justPublishedRef 记录本客户端刚发布的
 * 草稿原文，基线紧随其变化且草稿仍是发布文本时跳过 preserve、强制 reset
 * 到新基线；reload 在途期间用户又改了草稿（≠ 发布文本）则不强制，回落
 * 常规 preserve 判定。 */
export function useDraftBaselineSync(
  originalYaml: string,
  activeRevisionId: string | null | undefined,
  draftYaml: string,
  setDraftYaml: (value: string) => void,
  setViewState: Dispatch<SetStateAction<WorkflowStudioViewState>>,
  clearRevisionLoadError: () => void,
  hasPreservedDraft: boolean,
  justPublishedRef: { current: string | null }
) {
  const draftYamlRef = useRef(draftYaml)
  useEffect(() => {
    draftYamlRef.current = draftYaml
    // 草稿离开发布文本（用户又编辑/被其他路径改写）即失效，避免陈旧标记
    // 在之后的无关基线变化里误触发强制 reset。
    if (
      justPublishedRef.current !== null &&
      draftYaml !== justPublishedRef.current
    ) {
      justPublishedRef.current = null
    }
  }, [draftYaml, justPublishedRef])
  const originalYamlRef = useRef(originalYaml)
  const baselineLoadedRef = useRef(false)
  useEffect(() => {
    const previousOriginal = originalYamlRef.current
    originalYamlRef.current = originalYaml
    const currentDraft = draftYamlRef.current
    const firstRun = !baselineLoadedRef.current
    baselineLoadedRef.current = true
    const baselineChanged = originalYaml !== previousOriginal
    const justPublishedYaml = justPublishedRef.current
    if (baselineChanged && justPublishedYaml !== null) {
      justPublishedRef.current = null
    }
    const forceResetAfterPublish =
      baselineChanged &&
      justPublishedYaml !== null &&
      currentDraft === justPublishedYaml
    const preserveDirtyDraft =
      !forceResetAfterPublish &&
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
    justPublishedRef,
  ])

  useEffect(() => {
    if (hasPreservedDraft && !isDefinitionDirty(originalYaml, draftYaml)) {
      setViewState((current) => ({ ...current, hasPreservedDraft: false }))
    }
  }, [originalYaml, draftYaml, hasPreservedDraft, setViewState])
}
