import { useCallback, useEffect, useRef, useState } from 'react'
import { putWorkflowDraft } from '../../../api'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { DraftSaveController, IDLE_DRAFT_SAVE } from './draftSaveController'
import type { DraftSaveFlushResult, DraftSaveState } from './draftSaveController'
import { useDraftUnloadGuard } from './useDraftUnloadGuard'

export type {
  DraftSaveState,
  DraftSaveStatus,
  DraftSaveFlushResult,
} from './draftSaveController'
export { draftSaveText } from './draftSaveController'

export type DraftSaveControls = {
  state: DraftSaveState
  /** 取消 pending 的 debounce 立即 PUT 并返回其 promise（含重试链的终
   * 态）；keepalive 仅用于 pagehide。#429 四轮 P2-1：返回 promise 让
   * agent 发布确认可以 await 落盘完成后再读服务端草稿。#429 收尾 P2-1：
   * resolve 值是本次 flush 的终态（ok=false 即本次落盘失败——controller
   * 全路径 resolve 不 reject，失败只进 state；调用方读返回值而不是 React
   * 快照的 status，否则「await 期间 PUT 失败」会漏过守卫）。 */
  flushNow: (keepalive?: boolean) => Promise<DraftSaveFlushResult>
  hasUnsavedChanges: () => boolean
}

/** 草稿自动持久化：draftYaml 变化由 DraftSaveController debounce 后 PUT
 * workflow-draft；保存机制（requestId 在途作废、失败退避重试、pagehide
 * keepalive）集中在 draftSaveController.ts，本 hook 只做 React 接线并暴露
 * flushNow/hasUnsavedChanges 供保存按钮与页面离开防丢（useDraftUnloadGuard）
 * 使用。首次装载竞态规则（与 useWorkflowStudioDraft 的服务端草稿应用配套）：
 * 草稿查询到达（serverDraft !== undefined）且基线已知（originalYaml 非空）
 * 才视为 hydrated；hydrated 时把「已持久化基线」记为服务端草稿值（无草稿
 * 时记为基线 YAML），此前不发起任何 PUT —— 避免用初始基线覆盖服务端草稿。
 * hydrated 用 state 而不是 ref：GET 在途期间用户已编辑时，保存 effect 因
 * 未 hydrated 提前退出、之后 draftYaml 不再变化就不会重跑；hydrated 翻转
 * 作为依赖会触发一次「当前 draftYaml 与已持久化值」的差异评估，此时
 * lastPersisted 已是服务端草稿值，不会把基线误存上去。
 * serverDraftLoadError（GET 失败）不阻塞编辑，只合并进 state.loadError 做
 * 可见警示；未 hydrated 期间 hasUnsavedChanges 以「相对基线有改动」兜底，
 * 让 beforeunload 仍能拦截纯内存编辑的丢失。 */
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
  const draftYamlRef = useRef(draftYaml)
  const originalYamlRef = useRef(originalYaml)

  useEffect(() => {
    draftYamlRef.current = draftYaml
    originalYamlRef.current = originalYaml
  })

  // controller 生命周期与 workspace 切换重置；cleanup 清理计时器（pending
  // 的尾部编辑随 debounce 窗口丢弃，与旧行为一致；页面级离开由
  // useDraftUnloadGuard 的 flush 覆盖）。
  useEffect(() => {
    hydratedRef.current = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setState(IDLE_DRAFT_SAVE)
    setHydrated(false)
    if (!workspaceId) return
    const controller = new DraftSaveController((yaml, keepalive) =>
      keepalive
        ? putWorkflowDraft(workspaceId, yaml, { keepalive: true })
        : putWorkflowDraft(workspaceId, yaml)
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
    controllerRef.current?.hydrate(
      serverDraft.definition_yaml ?? originalYaml,
      serverDraft.updated_at
    )
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
      if (!controller || !hydratedRef.current) {
        // 未挂载/未 hydrated：无内容可发也无失败可言（与 controller 的
        // no-op 同语义——等待方继续自己的重读校准）。
        return Promise.resolve({ ok: true, state: IDLE_DRAFT_SAVE })
      }
      // error 态无 pending（重试已耗尽）：重新调度当前草稿让 flush 有内容可发。
      controller.schedule(draftYamlRef.current)
      return controller.flushNow({ keepalive })
    },
    []
  )

  const hasUnsavedChanges = useCallback(() => {
    const controller = controllerRef.current
    if (!controller) return false
    if (!hydratedRef.current) {
      // GET 在途/失败（未 hydrated）：编辑仅在本页内存，相对基线有改动即未保存。
      const yaml = draftYamlRef.current
      return !!yaml.trim() && yaml !== originalYamlRef.current
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
