import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchWorkflowDraft, putWorkflowDraft } from '../../api'
import type { WorkflowDraftStoreResponse } from '../../api/workflowDraft'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

export type DraftSaveStatus = 'idle' | 'saving' | 'saved' | 'error'
export type DraftSaveState = { status: DraftSaveStatus; savedAt: string | null }

const DEBOUNCE_MS = 800

const IDLE: DraftSaveState = { status: 'idle', savedAt: null }

/** 保存状态的一句话提示（顶栏 tooltip）：保存中/失败优先，否则带最近保存
 * 时间（HH:MM，本地时区）。 */
export function draftSaveText(save: DraftSaveState | undefined): string | null {
  if (!save) return null
  if (save.status === 'saving') return '草稿保存中…'
  if (save.status === 'error') return '草稿自动保存失败（编辑尚未持久化）'
  if (!save.savedAt) return null
  const at = new Date(save.savedAt)
  const hh = String(at.getHours()).padStart(2, '0')
  const mm = String(at.getMinutes()).padStart(2, '0')
  return `草稿已保存 ${hh}:${mm}`
}

/** 服务端草稿查询（GET workflow-draft）：definition_yaml 为 null 表示该
 * workspace 还没有持久化草稿；查询失败时 data 停留 undefined，草稿退回
 * 纯内存行为（与持久化上线前一致）。 */
export function useWorkflowDraftQuery(workspaceId: string | undefined) {
  return useQuery({
    queryKey: extraQueryKeys.workflowStudioDraft(workspaceId ?? ''),
    queryFn: () => fetchWorkflowDraft(workspaceId ?? ''),
    enabled: !!workspaceId,
  })
}

/** 草稿自动持久化：draftYaml 变化 debounce 后 PUT workflow-draft。
 * 首次装载竞态规则（与 useWorkflowStudioDraft 的服务端草稿应用配套）：
 * 草稿查询到达（serverDraft !== undefined）且基线已知（originalYaml 非空）
 * 才视为 hydrated；hydrated 时把「已持久化基线」记为服务端草稿值（无草稿
 * 时记为基线 YAML），此前不发起任何 PUT —— 避免用初始基线覆盖服务端草稿。
 * hydrated 之后 draftYaml 偏离已持久化值即自动保存（含 publish 后草稿回到
 * 新基线、重置草稿等路径），并发/连打退化为 last-write-wins。
 * hydrated 用 state 而不是 ref：GET 在途期间用户已编辑时，保存 effect 因
 * 未 hydrated 提前退出、之后 draftYaml 不再变化就不会重跑；hydrated 翻转
 * 作为依赖会触发一次「当前 draftYaml 与已持久化值」的差异评估，此时
 * lastPersisted 已是服务端草稿值，不会把基线误存上去。 */
export function useWorkflowDraftPersistence(
  workspaceId: string | undefined,
  draftYaml: string,
  originalYaml: string,
  serverDraft: WorkflowDraftStoreResponse | undefined
): DraftSaveState {
  const [saveState, setSaveState] = useState<DraftSaveState>(IDLE)
  const [hydrated, setHydrated] = useState(false)
  const lastPersistedRef = useRef<string | null>(null)
  const requestCounter = useRef(0)
  // 最新一次已发起 PUT 的 requestId（0 = 无在途写入）：回退到已持久化值时
  // 据此判断是否需要作废在途写入并补存当前值。
  const inFlightRef = useRef(0)

  useEffect(() => {
    lastPersistedRef.current = null
    inFlightRef.current = 0
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setSaveState(IDLE)
    setHydrated(false)
  }, [workspaceId])

  useEffect(() => {
    if (hydrated || serverDraft === undefined || !originalYaml) {
      return
    }
    lastPersistedRef.current = serverDraft.definition_yaml ?? originalYaml
    if (serverDraft.updated_at) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 记录服务端草稿的保存时间用于 tooltip
      setSaveState({ status: 'idle', savedAt: serverDraft.updated_at })
    }
    setHydrated(true)
  }, [hydrated, serverDraft, originalYaml])

  useEffect(() => {
    if (!workspaceId || !hydrated) return
    if (!draftYaml.trim()) return
    const revertedToPersisted = draftYaml === lastPersistedRef.current
    if (revertedToPersisted && inFlightRef.current === 0) return
    // 回退到已持久化值但仍有在途写入：新 requestId 作废纸上的响应（否则它
    // 会把 lastPersisted 更新成已撤销的值），并照常补存当前值，把服务端
    // 可能已收到的撤销值改回来。
    const requestId = (requestCounter.current += 1)
    const timer = setTimeout(() => {
      inFlightRef.current = requestId
      setSaveState((current) => ({ ...current, status: 'saving' }))
      putWorkflowDraft(workspaceId, draftYaml)
        .then((response) => {
          if (inFlightRef.current === requestId) inFlightRef.current = 0
          if (requestCounter.current !== requestId) return
          lastPersistedRef.current = draftYaml
          setSaveState({
            status: 'saved',
            savedAt: response.updated_at ?? null,
          })
        })
        .catch(() => {
          if (inFlightRef.current === requestId) inFlightRef.current = 0
          if (requestCounter.current !== requestId) return
          setSaveState((current) => ({ ...current, status: 'error' }))
        })
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [workspaceId, draftYaml, hydrated])

  return saveState
}
