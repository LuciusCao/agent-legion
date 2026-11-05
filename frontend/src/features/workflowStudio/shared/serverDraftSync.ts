import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { isServerDraftAdvanced } from './serverDraftAdvance'
import type { ServerDraftConflict } from './useServerDraftApply.types'

export type ServerDraftSyncInput = {
  serverDraft: WorkflowDraftStoreResponse | undefined
  originalYaml: string
  hydrated: boolean
  conflict: ServerDraftConflict | null
  controllerInConflict: () => boolean
}

export type ServerDraftSyncDecision =
  | { action: 'not-ready' | 'noop' }
  | { action: 'conflict'; conflict: ServerDraftConflict }
  | {
      action: 'hydrate'
      yaml: string
      updatedAt: string | null | undefined
      generation: number | null
      hash: string | null
    }

/** 服务端草稿 → 保存控制器的水合状态与纯决策。React 接线留在
 * useDraftServerSync；这里不持有控制器，也不发保存请求。
 *
 * 前进判定以代次为准，时间戳仅用于展示与混合部署回落。草稿行消失是
 * 「无存档」而非「更旧」：空态按 canonical 文本 + 代次幂等地重水合；
 * 未解决的 409 冲突优先于空态水合，避免清除本地未落盘编辑的保护态。 */
export class ServerDraftSyncTracker {
  private hydrated = false
  private hydratedAt: string | null = null
  private hydratedGeneration: number | null = null
  private nullBaselineHydrated: string | null = null

  reset(): void {
    this.hydrated = false
    this.hydratedAt = null
    this.hydratedGeneration = null
    this.nullBaselineHydrated = null
  }

  isHydrated(): boolean {
    return this.hydrated
  }

  evaluate(input: ServerDraftSyncInput): ServerDraftSyncDecision {
    const {
      serverDraft,
      originalYaml,
      hydrated,
      conflict,
      controllerInConflict,
    } = input
    if (serverDraft === undefined || !originalYaml) {
      return { action: 'not-ready' }
    }
    if (conflict) {
      this.hydratedAt = conflict.updatedAt || null
      if (conflict.generation != null) {
        this.hydratedGeneration = conflict.generation
      }
      return { action: 'conflict', conflict }
    }
    if (serverDraft.definition_yaml == null) {
      if (controllerInConflict()) return { action: 'noop' }
      const key = `${originalYaml}@${serverDraft.generation}`
      if (this.nullBaselineHydrated === key) return { action: 'noop' }
      this.nullBaselineHydrated = key
    } else {
      this.nullBaselineHydrated = null
      if (
        hydrated &&
        !isServerDraftAdvanced(
          serverDraft.generation,
          this.hydratedGeneration,
          serverDraft.updated_at,
          this.hydratedAt
        )
      ) {
        return { action: 'noop' }
      }
    }
    this.hydratedAt = serverDraft.updated_at ?? null
    this.hydratedGeneration = serverDraft.generation
    this.hydrated = true
    return {
      action: 'hydrate',
      yaml: serverDraft.definition_yaml ?? originalYaml,
      updatedAt: serverDraft.updated_at,
      generation: serverDraft.generation,
      hash: serverDraft.definition_hash ?? null,
    }
  }
}
