import { useCallback, useEffect, useRef, useState } from 'react'
import { putWorkflowDraft } from '../../../api'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { DraftSaveController } from './draftSaveController'
import {
  IDLE_DRAFT_SAVE,
  type DraftSaveFlushResult,
  type DraftSaveState,
} from './draftSaveTypes'
import { useDraftServerSync } from './useDraftServerSync'
import { useDraftUnloadGuard } from './useDraftUnloadGuard'
import type { ServerDraftConflict } from './useServerDraftApply'

export type { DraftSaveState } from './draftSaveTypes'
export { draftSaveText } from './draftSaveText'

export type DraftSaveControls = {
  state: DraftSaveState
  /* 取消 pending 的 debounce 立即 PUT 并返回其 promise；keepalive 仅用于
     pagehide。#429：resolve 值是本次 flush 的终态（ok=false 即本次落盘
     失败——controller 全路径 resolve 不 reject，失败只进 state；调用方读
     返回值而不是 React 快照的 status，否则「await 期间 PUT 失败」会漏过
     守卫）。 */
  flushNow: (keepalive?: boolean) => Promise<DraftSaveFlushResult>
  hasUnsavedChanges: () => boolean
}

/* 草稿自动持久化：draftYaml 变化由 DraftSaveController debounce 后 PUT
   workflow-draft；保存机制集中在 draftSaveController.ts，本 hook 只做 React
   接线。首次装载竞态：草稿查询到达且基线已知才 hydrated（hydrate/reapply/
   冲突接线在 useDraftServerSync），此时「已持久化基线」记为服务端草稿值
   （无草稿时记为基线 YAML），此前不发起任何 PUT——避免用初始基线覆盖
   服务端草稿。hydrated 翻转会触发一次保存差异评估（lastPersisted 已是
   服务端草稿值，不会误存基线）。serverDraftLoadError（GET 失败）不阻塞
   编辑，只合并进 state.loadError 做可见警示；未 hydrated 期间
   hasUnsavedChanges 以「相对基线有改动」兜底。 */
export function useWorkflowDraftPersistence(
  workspaceId: string | undefined,
  draftYaml: string,
  originalYaml: string,
  serverDraft: WorkflowDraftStoreResponse | undefined,
  serverDraftLoadError = false,
  consumeReapplyConflict: () => ServerDraftConflict | null = () => null
): DraftSaveControls {
  const [state, setState] = useState<DraftSaveState>(IDLE_DRAFT_SAVE)
  const controllerRef = useRef<DraftSaveController | null>(null)
  // draft/original 镜像 ref：flushNow/hasUnsavedChanges 的 useCallback 不依赖
  // 草稿值本身，经 ref 读最新值。
  const yamlRefs = useRef({ draft: draftYaml, original: originalYaml })

  useEffect(() => {
    yamlRefs.current = { draft: draftYaml, original: originalYaml }
  })

  /* controller 生命周期与 workspace 切换重置；cleanup 清理计时器（pending
     的尾部编辑随 debounce 窗口丢弃，与旧行为一致；页面级离开由
     useDraftUnloadGuard 的 flush 覆盖）。 */
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setState(IDLE_DRAFT_SAVE)
    if (!workspaceId) return
    const controller = new DraftSaveController(
      /* #633：expectedUpdatedAt 由 controller 维护（hydrate 记录基线、保存
         成功后推进），这里只透传——keepalive 分支同样携带 CAS 基线。 */
      (yaml, keepalive, expectedUpdatedAt) =>
        putWorkflowDraft(workspaceId, yaml, {
          ...(keepalive && { keepalive: true }),
          expectedUpdatedAt,
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

  const { hydrated, isHydrated } = useDraftServerSync(
    workspaceId,
    serverDraft,
    originalYaml,
    controllerRef,
    consumeReapplyConflict
  )

  useEffect(() => {
    if (!workspaceId || !hydrated) return
    controllerRef.current?.schedule(draftYaml)
  }, [workspaceId, draftYaml, hydrated])

  const flushNow = useCallback(
    (keepalive = false): Promise<DraftSaveFlushResult> => {
      const controller = controllerRef.current
      /* 未挂载/未 hydrated：no-op（与 controller 同语义——等待方继续自己的
         重读校准）。error 态无 pending（重试已耗尽）：重新调度让 flush 有
         内容可发。 */
      if (!controller || !isHydrated()) {
        return Promise.resolve({ ok: true, state: IDLE_DRAFT_SAVE })
      }
      controller.schedule(yamlRefs.current.draft)
      return controller.flushNow({ keepalive })
    },
    [isHydrated]
  )

  const hasUnsavedChanges = useCallback(() => {
    const controller = controllerRef.current
    if (!controller) return false
    if (!isHydrated()) {
      // GET 在途/失败（未 hydrated）：编辑仅在本页内存，相对基线有改动即未保存。
      const { draft, original } = yamlRefs.current
      return !!draft.trim() && draft !== original
    }
    return controller.hasUnsaved()
  }, [isHydrated])

  useDraftUnloadGuard({ flush: flushNow, hasUnsavedChanges })

  return {
    state: serverDraftLoadError ? { ...state, loadError: true } : state,
    flushNow,
    hasUnsavedChanges,
  }
}
