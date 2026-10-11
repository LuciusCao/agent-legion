/** #1121：发送失败恢复 composer 草稿（成功由 composer 乐观清空）——在途期间
 * 新输入的文字保留，失败的文本按时间序排在其前，什么都不丢。
 * 独立成文件是体积预算拆分（useInFlightSend 无余量，#1238 codex 四轮）。 */
export function restoreDraftText(
  setText: (updater: (current: string) => string) => void,
  sent: string,
  ok: boolean
) {
  if (!ok) setText((current) => (current.trim() ? `${sent}\n${current}` : sent))
}
