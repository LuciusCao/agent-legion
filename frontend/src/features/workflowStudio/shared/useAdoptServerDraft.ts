/** kimi review P1-2：冲突出口「采用服务端（Agent）版本」的 useCallback
 * 装配（从 useDraftServerSync 拆出，文件体积预算）——controller 经
 * hydrate 推进基线、清除冲突；onAdopt 写画布。#1143：serverHash 是被采用
 * 草稿的语义身份，随 hydrate 恢复成 savedHash；#1226：serverGeneration
 * 是其代次（采用后恢复为 CAS 基线）。 */

import { useCallback } from 'react'
import type { RefObject } from 'react'
import type { DraftSaveController } from './draftSaveController'
import type { AdoptServerDraft } from './draftSaveConflict'

export function useAdoptServerDraft(
  controllerRef: RefObject<DraftSaveController | null>
): AdoptServerDraft {
  return useCallback(
    (serverYaml, serverAt, onAdopt, serverHash, serverGeneration) => {
      controllerRef.current?.adoptServerDraft(
        serverYaml,
        serverAt,
        serverHash,
        serverGeneration
      )
      onAdopt?.(serverYaml)
    },
    [controllerRef]
  )
}
