import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import type { DraftSaveController } from './draftSaveController'
import type { AdoptServerDraft } from './draftSaveConflict'
import { ServerDraftSyncTracker } from './serverDraftSync'
import { useAdoptServerDraft } from './useAdoptServerDraft'
import type { ServerDraftConflict } from './useServerDraftApply'

/** 服务端草稿 → 保存控制器的 React 接线。首装/重应用水合、冲突呈现与
 * 空态重水合的状态和决策在 serverDraftSync.ts；本 hook 只保持 effect
 * 顺序并执行控制器命令。consumeConflict 须稳定；isHydrated 供无依赖的
 * flushNow/hasUnsavedChanges 读最新值，避免闭包过期。 */
export function useDraftServerSync(
  workspaceId: string | undefined,
  serverDraft: WorkflowDraftStoreResponse | undefined,
  originalYaml: string,
  controllerRef: RefObject<DraftSaveController | null>,
  consumeConflict: () => ServerDraftConflict | null
): {
  hydrated: boolean
  isHydrated: () => boolean
  adoptServerDraft: AdoptServerDraft
} {
  const [hydrated, setHydrated] = useState(false)
  const trackerRef = useRef(new ServerDraftSyncTracker())
  const isHydrated = useCallback(() => trackerRef.current.isHydrated(), [])
  useEffect(() => {
    trackerRef.current.reset()
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置 hydrate 状态
    setHydrated(false)
  }, [workspaceId])
  useEffect(() => {
    if (serverDraft === undefined || !originalYaml) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrated 翻转须触发一次保存差异评估
    if (!hydrated) setHydrated(true)
    const decision = trackerRef.current.evaluate({
      serverDraft,
      originalYaml,
      hydrated,
      conflict: hydrated ? consumeConflict() : null,
      controllerInConflict: () => controllerRef.current?.inConflict() ?? false,
    })
    if (decision.action === 'conflict') {
      const { yaml, updatedAt, hash, generation } = decision.conflict
      controllerRef.current?.enterConflict(yaml, updatedAt, hash, generation)
      return
    }
    if (decision.action !== 'hydrate') return
    controllerRef.current?.hydrate(
      decision.yaml,
      decision.updatedAt,
      decision.generation,
      decision.hash
    )
  }, [hydrated, serverDraft, originalYaml, controllerRef, consumeConflict])
  /* 采用服务端版本由 controller hydrate 推进基线并清冲突，onAdopt 写画布；
     keep-mine 留在 useWorkflowDraftPersistence（需要当前画布补调度）。 */
  const adoptServerDraft = useAdoptServerDraft(controllerRef)
  return { hydrated, isHydrated, adoptServerDraft }
}
