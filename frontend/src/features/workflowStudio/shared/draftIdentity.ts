/** 草稿身份核对（#1196 从 draftSaveTypes.ts 拆出，文件体积预算）：
 * 「服务端已落盘内容的语义身份」的真值判定。 */

import type { DraftSaveState } from './draftSaveTypes'

/** #1143/#1196：草稿卡 hash 短路的唯一判据——「编辑器内容 content 的服务端
 * 语义身份就是 savedHash」须由真值确认：content 逐字节等于 savedHash 描述
 * 的已落盘内容（savedYaml）。在此之上保留既有前置（settled、非空白、两侧
 * hash 均存在且相等）。hydrate/adopt 时状态推进与画布写入不在同一批次，
 * 可出现一帧「settled + savedHash=H_D 但画布 ≠ D」——仅凭 status 会误短路
 * 隐藏分歧提示；有 savedYaml 核对后该帧回落提示。 */
export function savedIdentityConfirms(
  save: DraftSaveState | undefined,
  content: string,
  cardHash: string | null
): boolean {
  if (!save || !cardHash || !save.savedHash) return false
  const settled = save.status === 'saved' || save.status === 'idle'
  return (
    settled &&
    content.trim() !== '' &&
    content === save.savedYaml &&
    cardHash === save.savedHash
  )
}
