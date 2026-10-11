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
 * 4. #1226 四轮 P1 草稿行消失：发布成功删除草稿行后重取到「无草稿」——
 *    这不是「更旧的草稿」（updated_at 为 null 永远不比 hydratedAt 新，
 *    时间戳守卫不适用），而是「无存档」：必须重新 hydrate 到当前基线
 *    （null 时间戳 → 下一次保存以 never-saved 竞争），否则保存控制器仍持
 *    已删草稿的旧基线，画布 reset 到 canonical 后的尾随 schedule 会被判成
 *    新编辑，debounce 到期 PUT 立刻重建刚删的草稿行。
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
): {
  hydrated: boolean
  isHydrated: () => boolean
  adoptServerDraft: (
    serverYaml: string,
    serverAt: string | null,
    onAdopt?: (serverYaml: string) => void
  ) => void
} {
  const [hydrated, setHydrated] = useState(false)
  const hydratedRef = useRef(false)
  const hydratedAtRef = useRef<string | null>(null)
  /* #1226 四轮 P1：「无草稿」重 hydrate 的幂等闸——记录已按哪份基线补过
     null-draft hydrate；同一基线的重复重取（effect 因非稳定回调引用等
     原因反复触发）跳过，否则每次 hydrate 的 setState 引发重渲染会构成
     effect → setState → effect 循环。 */
  const nullBaselineHydratedRef = useRef<string | null>(null)
  const isHydrated = useCallback(() => hydratedRef.current, [])
  useEffect(() => {
    hydratedRef.current = false
    nullBaselineHydratedRef.current = null
    // eslint-disable-next-line react-hooks/set-state-in-effect -- workspace 切换时重置 hydrate 状态
    setHydrated(false)
  }, [workspaceId])
  useEffect(() => {
    if (serverDraft === undefined || !originalYaml) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrated 翻转须触发一次保存差异评估
    if (!hydrated) setHydrated(true)
    const conflict = hydrated ? consumeConflict() : null
    if (conflict) {
      controllerRef.current?.enterConflict(
        conflict.yaml,
        conflict.updatedAt,
        conflict.hash
      )
      hydratedAtRef.current = conflict.updatedAt || null
      return
    }
    if (serverDraft.definition_yaml == null) {
      // #1226 四轮 P1：草稿行消失（发布删除）不是「更旧」而是「无存档」，
      // 时间戳守卫不适用——按当前基线重 hydrate（null 时间戳 → 下一次保存
      // 以 never-saved 竞争），同一基线只补一次。
      if (nullBaselineHydratedRef.current === originalYaml) return
      nullBaselineHydratedRef.current = originalYaml
    } else {
      nullBaselineHydratedRef.current = null
      if (
        hydrated &&
        !isServerDraftNewer(serverDraft.updated_at, hydratedAtRef.current)
      ) {
        return
      }
    }
    controllerRef.current?.hydrate(
      serverDraft.definition_yaml ?? originalYaml,
      serverDraft.updated_at,
      serverDraft.definition_hash ?? null
    )
    hydratedAtRef.current = serverDraft.updated_at ?? null
    hydratedRef.current = true
  }, [hydrated, serverDraft, originalYaml, controllerRef, consumeConflict])
  /* kimi review P1-2：冲突出口——采用服务端（Agent）版本（hydrate 推进
     基线、清除冲突；onAdopt 写画布）或显式 keep-mine 继续保存。#1143：
     serverHash 是被采用草稿的语义身份，随 hydrate 恢复成 savedHash。 */
  const adoptServerDraft = useCallback(
    (
      serverYaml: string,
      serverAt: string | null,
      onAdopt?: (yaml: string) => void,
      serverHash?: string | null
    ) => {
      controllerRef.current?.adoptServerDraft(serverYaml, serverAt, serverHash)
      onAdopt?.(serverYaml)
    },
    [controllerRef]
  )
  // #804 P1-A：resolveConflict 移到 useWorkflowDraftPersistence（keep-mine
  // 需要当前画布内容补调度，serverSync 层拿不到 draftYaml）。
  return { hydrated, isHydrated, adoptServerDraft }
}
