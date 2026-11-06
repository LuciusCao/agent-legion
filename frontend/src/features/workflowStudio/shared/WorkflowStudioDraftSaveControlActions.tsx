import { useStudioState } from './studioStateContext'

/* 保存警示的回调接线（容器与抽屉警示横幅共用，轮 4 P2-F / 轮 5 P2）：
   冲突二选一 + error 终态的显式重试（flushDraftSave 在 error 态会重新
   schedule 当前内容再 flush——persistence 层既有语义）。 */
export function useDraftSaveConflictActions() {
  const studio = useStudioState()
  return {
    onAdoptServer: () => {
      const conflict = studio.draftSave?.conflictDraftYaml
      if (conflict == null) {
        // #1226 复审 P1：空内容冲突 = 发布删行后的在途写入被 409——服务端
        // 没有草稿可采用。采用 = 画布回到已发布 canonical，CAS 基线推进到
        // 冲突携带的发布后代次；不能仅解除警示（那会让 controller 收敛
        // saved/idle 而画布仍是未落盘文本，关页静默丢失）。
        const generation = studio.draftSave?.conflictDraftGeneration
        studio.adoptServerDraft(studio.originalYaml, null, null, generation)
        return
      }
      // #1143：带上冲突草稿的语义身份——采用后 savedHash 恢复为服务端
      // 草稿身份，草稿卡核对不应对刚采用的内容误报不一致。
      // #1177 codex P2：conflictDraftHash 的 undefined/null 分开传递——
      // undefined（冲突事件未带 hash 字段）由 controller 按内容是否偏离
      // 基线决定保留/清除旧 hash（#1196 归一）；null（服务端明确无身份）
      // 清除旧 hash。
      studio.adoptServerDraft(
        conflict,
        studio.draftSave?.savedAt ?? null,
        studio.draftSave?.conflictDraftHash,
        studio.draftSave?.conflictDraftGeneration
      )
    },
    onKeepMine: () => studio.resolveConflict(true),
    onRetrySave: () => void studio.flushDraftSave(),
  }
}
