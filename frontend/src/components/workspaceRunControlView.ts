/** 顶栏运行按钮的三态视图（#961）：读取中 / 状态未知（拉取失败且无缓存值）/ 已知的
 * 暂停或运行。拉取中与失败都不得冒充「已暂停」。 */
export type RunControlView = {
  kind: 'loading' | 'unknown' | 'known'
  paused: boolean
  label: string
  ariaLabel: string
  icon: string
}

export function runControlView(status: {
  data: boolean | undefined
  isError: boolean
}): RunControlView {
  // 后台刷新失败但已有上次成功值时继续显示该值（窗口聚焦刷新偶发失败
  // 不闪「状态未知」）；只有从未拿到值且失败才是「状态未知」。
  if (status.isError && status.data === undefined) {
    const ariaLabel = '重新获取运行状态'
    return {
      kind: 'unknown',
      paused: false,
      label: '状态未知',
      ariaLabel,
      icon: 'help',
    }
  }
  if (status.data === undefined) {
    const ariaLabel = '运行状态读取中'
    return {
      kind: 'loading',
      paused: false,
      label: '读取中',
      ariaLabel,
      icon: 'hourglass_empty',
    }
  }
  return status.data
    ? {
        kind: 'known',
        paused: true,
        label: '已暂停',
        ariaLabel: '恢复运行',
        icon: 'play_arrow',
      }
    : {
        kind: 'known',
        paused: false,
        label: '运行中',
        ariaLabel: '暂停运行',
        icon: 'pause',
      }
}
