// Studio 对话保留策略（#1041）的前端现算：实例配置的保留天数随会话列表
// 下发（0 = 未配置），归档行倒计时 = 归档时间 + 保留天数 − 现在。后端
// sweeper 按小时级节奏清理，所以剩余不足一天时显示「即将清理」。

const DAY_MS = 86_400_000

/** 剩余整天数（向上取整、下限 0）；未配置保留策略或缺时间戳时为 null，
 * 调用方据此不渲染任何倒计时。 */
export function retentionDaysLeft(
  stamp: string | null | undefined,
  retentionDays: number,
  now: number = Date.now()
): number | null {
  if (retentionDays <= 0 || !stamp) return null
  const purgeAt = Date.parse(stamp) + retentionDays * DAY_MS
  if (Number.isNaN(purgeAt)) return null
  return Math.max(0, Math.ceil((purgeAt - now) / DAY_MS))
}

/** 归档行倒计时文案。 */
export function retentionCountdownLabel(daysLeft: number): string {
  return daysLeft <= 0 ? '即将清理' : `${daysLeft} 天后清理`
}

/** 归档 / 删除操作时的提示后缀；未配置保留策略时为空串。 */
export function retentionNotice(retentionDays: number): string {
  return retentionDays > 0 ? `将于 ${retentionDays} 天后自动清理` : ''
}
