import { useEffect, useMemo, useRef, useState } from 'react'
import { ServerDraftApplyTracker } from './serverDraftReapply'

export type ServerDraftConflict = { yaml: string; updatedAt: string }

export type ServerDraftApplyControls = {
  /* touched-aware setter：经它（用户编辑/聊天应用/重置/采用历史版本）的
     写入才算「用户碰过」；useDraftBaselineSync 的内部 reset 不算。 */
  setDraftYaml: (value: string) => void
  /* #633 codex review P1-2：服务端草稿前进但用户有本地编辑时挂起的冲突
     通知（yaml = 服务端草稿，updatedAt = 其 updated_at）；由保存层消费成
     conflict 态。读取即消费（再次调用返回 null）。 */
  consumeConflict: () => ServerDraftConflict | null
}

/* 服务端草稿的应用 + 用户编辑追踪（跟踪器在 serverDraftReapply.ts）。
   草稿查询到达且基线已知后，未触碰就用服务端草稿替换基线；本 effect
   在基线同步之后注册，无论两个查询谁先返回，最终都是服务端草稿胜出。
   #633：应用以 updated_at 为准——重取到更新的草稿时，用户无本地编辑则
   重应用到画布，有编辑则挂起冲突通知（绝不覆盖）。 */
export function useServerDraftApply(
  workspaceId: string | undefined,
  originalYaml: string,
  serverDraftYaml: string | null | undefined,
  serverDraftUpdatedAt: string | null | undefined,
  setDraftYamlState: (value: string) => void,
  canvasYaml?: string
): ServerDraftApplyControls {
  // workspace 切换重建跟踪器（清空 appliedAt/touched/冲突挂起）；两个 effect
  // 的注册顺序保证 reset 先于 evaluate 跑。
  const trackerRef = useRef(new ServerDraftApplyTracker())
  const [, forceRender] = useState(0)
  useEffect(() => {
    trackerRef.current = new ServerDraftApplyTracker()
  }, [workspaceId])
  useEffect(() => {
    if (serverDraftYaml === undefined || !originalYaml) return
    const outcome = trackerRef.current.evaluate(
      serverDraftYaml,
      serverDraftUpdatedAt,
      setDraftYamlState,
      canvasYaml
    )
    // 冲突挂起需要一次重渲染让保存层 effect 跑起来；apply/no-op 不需要。
    if (outcome === 'conflict') forceRender((count) => count + 1)
  }, [
    serverDraftYaml,
    serverDraftUpdatedAt,
    originalYaml,
    setDraftYamlState,
    canvasYaml,
  ])
  return useMemo(
    () => ({
      setDraftYaml: (value: string) => {
        trackerRef.current.markTouched()
        setDraftYamlState(value)
      },
      consumeConflict: () => trackerRef.current.consumeConflict(),
    }),
    [setDraftYamlState]
  )
}
