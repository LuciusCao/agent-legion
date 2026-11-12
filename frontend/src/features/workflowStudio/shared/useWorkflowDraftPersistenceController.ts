import { useEffect, useRef, useState } from 'react'
import { putWorkflowDraft } from '../../../api'
import { DraftSaveController } from './draftSaveController'
import { IDLE_DRAFT_SAVE, type DraftSaveState } from './draftSaveTypes'

/* controller 生命周期与 workspace 切换重置；cleanup 清理计时器（pending
   的尾部编辑随 debounce 窗口丢弃，与旧行为一致；页面级离开由
   useDraftUnloadGuard 的 flush 覆盖）。 */
export function useWorkflowDraftPersistenceController(
  workspaceId: string | undefined
) {
  const [state, setState] = useState<DraftSaveState>(IDLE_DRAFT_SAVE)
  const controllerRef = useRef<DraftSaveController | null>(null)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setState(IDLE_DRAFT_SAVE)
    if (!workspaceId) return
    const controller = new DraftSaveController(
      /* #633 + #1226 五轮收口：baseGeneration 由 controller 维护（hydrate
         记录基线代次、保存成功后推进），这里只透传——keepalive 分支同样
         携带 CAS 基线。 */
      (yaml, keepalive, baseGeneration) =>
        putWorkflowDraft(workspaceId, yaml, {
          ...(keepalive && { keepalive: true }),
          baseGeneration,
        })
    )
    controllerRef.current = controller
    const unsubscribe = controller.subscribe(setState)
    return () => {
      unsubscribe()
      controller.dispose()
      controllerRef.current = null
    }
  }, [workspaceId])

  return { controllerRef, state }
}
