import type { DraftSaveState } from './draftSaveTypes'

/** 保存状态的一句话提示（顶栏状态文本）：冲突/GET 失败警示、saving/error/
 * pending 优先，否则带最近保存时间（HH:MM，本地时区）。从
 * draftSaveController.ts 拆出（#633 文件体积预算）。 */
export function draftSaveText(save: DraftSaveState | undefined): string | null {
  if (!save) return null
  if (save.conflict)
    // kimi review P2-8：冲突文案带行动指引——服务端（Agent）保存了新草稿，
    // 本页未保存的编辑保留在画布，自动保存已挂起，需在两版之间做选择。
    return 'Agent 已保存新的草稿版本；本页编辑未落盘，自动保存已暂停——请选择采用 Agent 版本或保留本页编辑'
  if (save.loadError) return '草稿服务不可用，编辑仅保留在本页内存'
  if (save.status === 'saving') return '草稿保存中…'
  if (save.status === 'error') return '草稿保存失败，将自动重试'
  if (save.status === 'pending') return '草稿有未保存更改'
  if (!save.savedAt) return null
  const at = new Date(save.savedAt)
  const hh = String(at.getHours()).padStart(2, '0')
  const mm = String(at.getMinutes()).padStart(2, '0')
  return `草稿已保存 ${hh}:${mm}`
}
