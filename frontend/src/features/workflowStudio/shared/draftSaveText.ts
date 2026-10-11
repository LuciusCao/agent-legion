import type { DraftSaveState } from './draftSaveTypes'

/** 保存状态的一句话提示（顶栏状态文本）：冲突/GET 失败警示、saving/error
 * 常驻；#804 定案——pending（debounce 窗口内）与 saved（成功即隐）不再
 * 占用岛面，返回 null。从 draftSaveController.ts 拆出（#633 文件体积
 * 预算）。 */
export function draftSaveText(save: DraftSaveState | undefined): string | null {
  if (!save) return null
  if (save.conflict)
    // kimi review P2-8：冲突文案带行动指引——服务端（Agent）保存了新草稿，
    // 本页未保存的编辑保留在画布，自动保存已挂起，需在两版之间做选择。
    return 'Agent 已保存新的草稿版本；本页编辑未落盘，自动保存已暂停——请选择采用 Agent 版本或保留本页编辑'
  if (save.loadError) return '草稿服务不可用，编辑仅保留在本页内存'
  if (save.status === 'saving') return '草稿保存中…'
  // #1204：终态（4xx 客户端拒绝 / 退避耗尽）永不自行重试，文案不承诺
  // 「将自动重试」，改指显式出口（修改内容或点重试按钮）。
  if (save.status === 'error')
    return save.saveError === 'terminal'
      ? '草稿保存失败，不会自动重试——请修改内容或点击重试'
      : '草稿保存失败，将自动重试'
  return null
  return null
}
