import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import type { DraftSaveController } from './draftSaveController'
import { isServerDraftNewer } from './serverDraftReapply'
import type { ServerDraftConflict } from './useServerDraftApply'

/** #633：服务端草稿 → 保存控制器的接线（从 useWorkflowDraftPersistence
 * 拆出，文件体积预算）。三段职责共用一个 updated_at 真值：
 *
 * 1. 首装 hydrate：草稿查询到达且基线已知，把「已持久化基线」记为服务端
 *    草稿值（无草稿时记为基线 YAML），翻转 hydrated（保存 effect 依赖它
 *    做首次差异评估）。
 * 2. #633 codex review P1-2 重应用：turn-end 失效重取到更新的草稿且画布
 *    已采用（用户无本地编辑，useServerDraftApply 已写入）→ re-hydrate 推进
 *    lastPersisted/lastPersistedAt 到服务端真值，下一次 PUT 以新基线竞争。
 * 3. 冲突呈现：用户有本地编辑时经 consumeConflict 拿到通知 →
 *    enterConflict 进入 conflict 态（编辑保留，conflictDraftYaml =
 *    服务端草稿），基线同样推进。
 *
 * 同一 updated_at 不会重复 hydrate（isServerDraftNewer 为 false）；hydratedAt
 * 由本 hook 持有，与 useServerDraftApply 的画布采用共用同一时间戳。
 * consumeConflict 须稳定（useCallback）。返回的 isHydrated 供无依赖的
 * useCallback（flushNow/hasUnsavedChanges）读最新值，避免闭包过期。 */
export function useDraftServerSync(
  workspaceId: string | undefined,
  serverDraft: WorkflowDraftStoreResponse | undefined,
  originalYaml: string,
  controllerRef: RefObject<DraftSaveController | null>,
  consumeConflict: () => ServerDraftConflict | null
): { hydrated: boolean; isHydrated: () => boolean } {
  const [hydrated, setHydrated] = useState(false)
  const hydratedRef = useRef(false)
  const hydratedAtRef = useRef<string | null>(null)
  const isHydrated = useCallback(() => hydratedRef.current, [])
  useEffect(() => {
    hydratedRef.current = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置 hydrate 状态
    setHydrated(false)
  }, [workspaceId])
  useEffect(() => {
    if (serverDraft === undefined || !originalYaml) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrated 翻转须触发一次保存差异评估
    if (!hydrated) setHydrated(true)
    const conflict = hydrated ? consumeConflict() : null
    if (conflict) {
      controllerRef.current?.enterConflict(conflict.yaml, conflict.updatedAt)
      hydratedAtRef.current = conflict.updatedAt || null
      return
    }
    if (
      hydrated &&
      !isServerDraftNewer(serverDraft.updated_at, hydratedAtRef.current)
    ) {
      return
    }
    controllerRef.current?.hydrate(
      serverDraft.definition_yaml ?? originalYaml,
      serverDraft.updated_at
    )
    hydratedAtRef.current = serverDraft.updated_at ?? null
    hydratedRef.current = true
  }, [hydrated, serverDraft, originalYaml, controllerRef, consumeConflict])
  return { hydrated, isHydrated }
}
