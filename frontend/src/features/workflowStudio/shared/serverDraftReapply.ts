/** #633 codex review P1-2：服务端草稿重应用（turn-end 失效后）的状态跟踪
 * 器——纯决策与前进判定在 serverDraftAdvance.ts（拆出，文件体积预算），
 * 这里持有已应用基线（updated_at + 代次）与「用户碰过」标记。 */

import {
  decideServerDraftReapply,
  type ServerDraftConflictPayload,
} from './serverDraftAdvance'

/** 非React的应用跟踪器：记录已应用的 updated_at 与代次（#1226 复审 P2：
 * 前进判定以代次为准）与「用户碰过」标记，
 * 暴露 evaluate（供 effect 调用）与 markTouched（供 setter 调用）。
 * workspace 切换由外部重建实例（useServerDraftApply 的 reset effect）。 */
export class ServerDraftApplyTracker {
  private appliedAt: string | null = null
  private appliedGeneration: number | null = null
  private touched = false
  private conflict: ServerDraftConflictPayload | null = null

  markTouched(): void {
    this.touched = true
    this.conflict = null
  }

  consumeConflict(): ServerDraftConflictPayload | null {
    const conflict = this.conflict
    this.conflict = null
    return conflict
  }

  /** 评估一次服务端草稿：apply 时写画布并推进 appliedAt；conflict 时挂起
   * 冲突通知（由 consumeConflict 消费）。canvasYaml 参与 own-save 回显
   * 判定（kimi review P1-1）：服务端草稿与画布一致时走 apply（内容相同，
   * 实际只是推进基线），不误报冲突。#1143：serverDraftHash 随草稿传入，
   * 冲突通知携带它（保存层存进 conflictDraftHash，采用服务端版本时恢复）。 */
  evaluate(
    serverDraftYaml: string | null | undefined,
    serverDraftUpdatedAt: string | null | undefined,
    applyToCanvas: (yaml: string) => void,
    canvasYaml?: string,
    serverDraftHash?: string | null,
    serverDraftGeneration?: number | null
  ): 'apply' | 'conflict' | 'noop' {
    const decision = decideServerDraftReapply({
      serverDraftYaml,
      serverDraftUpdatedAt,
      serverDraftHash,
      serverDraftGeneration,
      appliedUpdatedAt: this.appliedAt,
      appliedGeneration: this.appliedGeneration,
      userTouched: this.touched,
      canvasYaml,
    })
    if (decision.action === 'apply') {
      this.appliedAt = decision.updatedAt
      // #1226 复审 P2：代次随应用推进；缺失（混合部署旧响应）不清空已知值。
      if (serverDraftGeneration != null)
        this.appliedGeneration = serverDraftGeneration
      this.conflict = null
      // codex R4 P2: an apply IS the server acknowledging a baseline the
      // canvas now matches (own-save echo, or an explicit adopt). The local
      // edit that set `touched` is confirmed on the server — keeping the
      // flag would misclassify the NEXT different agent update as "local
      // unsaved edits" and raise a phantom conflict.
      this.touched = false
      applyToCanvas(decision.yaml)
      return 'apply'
    }
    if (decision.action === 'conflict') {
      const { yaml, updatedAt, hash, generation } = decision
      this.conflict = { yaml, updatedAt, hash, generation }
      return 'conflict'
    }
    return 'noop'
  }
}
