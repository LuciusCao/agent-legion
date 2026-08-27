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
 * 新基线、重置草稿等路径），并发/连打退化为 last-write-wins。 */
export function useWorkflowDraftPersistence(
  workspaceId: string | undefined,
  draftYaml: string,
  originalYaml: string,
  serverDraft: WorkflowDraftStoreResponse | undefined
): DraftSaveState {
  const [saveState, setSaveState] = useState<DraftSaveState>(IDLE)
  const hydratedRef = useRef(false)
  const lastPersistedRef = useRef<string | null>(null)
  const requestCounter = useRef(0)

  useEffect(() => {
    hydratedRef.current = false
    lastPersistedRef.current = null
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置保存状态
    setSaveState(IDLE)
  }, [workspaceId])

  useEffect(() => {
    if (hydratedRef.current || serverDraft === undefined || !originalYaml) {
      return
    }
    hydratedRef.current = true
    lastPersistedRef.current = serverDraft.definition_yaml ?? originalYaml
    if (serverDraft.updated_at) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 记录服务端草稿的保存时间用于 tooltip
      setSaveState({ status: 'idle', savedAt: serverDraft.updated_at })
    }
  }, [serverDraft, originalYaml])

  useEffect(() => {
    if (!workspaceId || !hydratedRef.current) return
    if (!draftYaml.trim() || draftYaml === lastPersistedRef.current) return
    const requestId = (requestCounter.current += 1)
    const timer = setTimeout(() => {
      setSaveState((current) => ({ ...current, status: 'saving' }))
      putWorkflowDraft(workspaceId, draftYaml)
        .then((response) => {
          if (requestCounter.current !== requestId) return
          lastPersistedRef.current = draftYaml
          setSaveState({
            status: 'saved',
            savedAt: response.updated_at ?? null,
          })
        })
        .catch(() => {
          if (requestCounter.current !== requestId) return
          setSaveState((current) => ({ ...current, status: 'error' }))
        })
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [workspaceId, draftYaml])

  return saveState
}
