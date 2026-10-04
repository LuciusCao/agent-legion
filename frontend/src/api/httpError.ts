/** 非 2xx 响应 → 带 `status` / `detail` 的 Error（自 core.ts 拆出，#719）。 */
export async function httpError(response: Response): Promise<Error> {
  const text = await response.text()
  let message: string
  let detail: unknown
  const prefix = `HTTP ${response.status}`
  try {
    const json = JSON.parse(text)
    detail = json.detail
    const d = json.detail as
      | string
      | { message?: string; code?: string; limit?: number }
      | undefined
    // #467：结构化 detail（部分创建失败）取 message；字符串直传。
    // #712：批量选择超上限给本地化提示（全选/按筛选批量最常触发）。
    const inline =
      typeof d === 'string'
        ? d
        : d?.code === 'batch_selection_too_large'
          ? `所选任务超过单次批量操作上限（${d.limit ?? ''} 个），请缩小筛选范围或分批操作`
          : d?.message
    message = (typeof inline === 'string' && inline) || json.message || prefix
  } catch {
    message = `${prefix}: ${text.slice(0, 200)}`
  }
  return Object.assign(new Error(message), { status: response.status, detail })
}
