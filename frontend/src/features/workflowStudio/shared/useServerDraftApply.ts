import { useEffect, useMemo, useRef, useState } from 'react'
import { ServerDraftApplyTracker } from './serverDraftReapply'
import type {
  ServerDraftApplyControls,
  ServerDraftApplyInput,
} from './useServerDraftApply.types'

export type { ServerDraftConflict } from './useServerDraftApply.types'

/* 服务端草稿的应用 + 用户编辑追踪（跟踪器在 serverDraftReapply.ts）。
   草稿查询到达且基线已知后，未触碰就用服务端草稿替换基线；本 effect
   在基线同步之后注册，无论两个查询谁先返回，最终都是服务端草稿胜出。
   前进判定以代次为准：用户无本地编辑则重应用，有编辑则挂起冲突通知。 */
export function useServerDraftApply(
  input: ServerDraftApplyInput
): ServerDraftApplyControls {
  // workspace 切换重建跟踪器；两个 effect 的顺序保证 reset 先于 evaluate。
  const trackerRef = useRef(new ServerDraftApplyTracker())
  const setDraftYamlState = input.setDraftYamlState
  const [, forceRender] = useState(0)
  useEffect(() => {
    trackerRef.current = new ServerDraftApplyTracker()
  }, [input.workspaceId])
  useEffect(() => {
    if (input.serverDraftYaml === undefined || !input.originalYaml) return
    const outcome = trackerRef.current.evaluate(input)
    // 冲突挂起需要一次重渲染让保存层 effect 跑起来；apply/no-op 不需要。
    if (outcome === 'conflict') forceRender((count) => count + 1)
    // input 仅是参数容器；逐字段依赖避免对象新引用触发无意义重放。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    input.serverDraftYaml,
    input.serverDraftUpdatedAt,
    input.serverDraftHash,
    input.serverDraftGeneration,
    input.originalYaml,
    setDraftYamlState,
    input.canvasYaml,
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
