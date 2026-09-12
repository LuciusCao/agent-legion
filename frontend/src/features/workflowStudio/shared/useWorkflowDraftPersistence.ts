import { useCallback, useEffect, useRef, useState } from 'react'
import { putWorkflowDraft } from '../../../api'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { DraftSaveController } from './draftSaveController'
import {
  IDLE_DRAFT_SAVE,
  type DraftSaveFlushResult,
  type DraftSaveState,
} from './draftSaveTypes'
import { useDraftUnloadGuard } from './useDraftUnloadGuard'

export type {
  DraftSaveState,
  DraftSaveStatus,
  DraftSaveFlushResult,
} from './draftSaveTypes'
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
   接线。首次装载竞态：草稿查询到达且基线已知才 hydrated，此时把「已持久化
   基线」记为服务端草稿值（无草稿时记为基线 YAML），此前不发起任何 PUT——
   避免用初始基线覆盖服务端草稿。hydrated 用 state 而不是 ref：GET 在途
   期间用户已编辑时，保存 effect 因未 hydrated 提前退出，hydrated 翻转作为
   依赖会触发一次差异评估（lastPersisted 已是服务端草稿值，不会误存基线）。
   serverDraftLoadError（GET 失败）不阻塞编辑，只合并进 state.loadError 做
   可见警示；未 hydrated 期间 hasUnsavedChanges 以「相对基线有改动」兜底。 */
export function useWorkflowDraftPersistence(
  workspaceId: string | undefined,
  draftYaml: string,
  originalYaml: string,
  serverDraft: WorkflowDraftStoreResponse | undefined,
  serverDraftLoadError = false
): DraftSaveControls {
  const [state, setState] = useState<DraftSaveState>(IDLE_DRAFT_SAVE)
  const [hydrated, setHydrated] = useState(false)
  const controllerRef = useRef<DraftSaveController | null>(null)
  const hydratedRef = useRef(false)
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
    hydratedRef.current = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setState(IDLE_DRAFT_SAVE)
    setHydrated(false)
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

  useEffect(() => {
    if (hydrated || serverDraft === undefined || !originalYaml) return
    const persisted = serverDraft.definition_yaml ?? originalYaml
    controllerRef.current?.hydrate(persisted, serverDraft.updated_at)
    hydratedRef.current = true
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrated 翻转须触发一次保存差异评估（见 hook docstring）
    setHydrated(true)
  }, [hydrated, serverDraft, originalYaml])

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
      if (!controller || !hydratedRef.current) {
        return Promise.resolve({ ok: true, state: IDLE_DRAFT_SAVE })
      }
      controller.schedule(yamlRefs.current.draft)
      return controller.flushNow({ keepalive })
    },
    []
  )

  const hasUnsavedChanges = useCallback(() => {
    const controller = controllerRef.current
    if (!controller) return false
    if (!hydratedRef.current) {
      /* GET 在途/失败（未 hydrated）：编辑仅在本页内存，相对基线有改动即未保存。 */
      const { draft, original } = yamlRefs.current
      return !!draft.trim() && draft !== original
    }
    return controller.hasUnsaved()
  }, [])

  useDraftUnloadGuard({ flush: flushNow, hasUnsavedChanges })

  return {
    state: serverDraftLoadError ? { ...state, loadError: true } : state,
    flushNow,
    hasUnsavedChanges,
  }
}
