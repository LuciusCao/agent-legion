/**
 * 「定制预览」覆盖面板的定位/尺寸契约（#615 方向 A）：从组件抽出以保持
 * 对话框在体积预算内，也避免 sx 对象字面量的条件键重复（TS2783）。
 * - 根层 pointerEvents:none + Paper auto：命中面板之外的点击/滚动全部交给
 *   底层页面（非模态的交互前提，配合 hideBackdrop/disableScrollLock）。
 * - 宽屏（≥1200px，MUI lg）：右侧通栏停靠，宽度收敛在 job progress 列
 *   （30vw）内，不遮左栏预览区；
 * - 窄屏（<1200px）：右下浮动卡片降级—— dock 宽度已不够聊天可用性，浮动
 *   卡片 + 折叠小条保证左栏仍可露出（底层全程可滚动交互）；
 * - 折叠：Paper 缩成右下角小条（内容换成展开按钮，会话保持存活）。
 */
import type { SxProps, Theme } from '@mui/material/styles'

/** Dialog 根层：右对齐容器；窄屏时容器底部对齐（浮动卡片贴右下）。 */
export const overlayDialogSx: SxProps<Theme> = {
  pointerEvents: 'none',
  '& .MuiDialog-container': {
    justifyContent: 'flex-end',
    alignItems: { xs: 'flex-end', lg: 'stretch' },
  },
}

/** Paper 尺寸：折叠为小条 / 宽屏通栏停靠 / 窄屏浮动卡片三态。 */
export function overlayPaperSx(collapsed: boolean): SxProps<Theme> {
  if (collapsed) {
    return {
      pointerEvents: 'auto',
      position: 'fixed',
      right: 16,
      bottom: 16,
      margin: 0,
      borderRadius: 999,
    }
  }
  return (theme: Theme) => ({
    pointerEvents: 'auto',
    margin: 0,
    width: 'calc(30vw - 20px)',
    minWidth: 340,
    maxWidth: 'none',
    height: '100%',
    maxHeight: '100%',
    borderRadius: 0,
    display: 'flex',
    flexDirection: 'column',
    [theme.breakpoints.down('lg')]: {
      width: 'min(460px, calc(100vw - 24px))',
      minWidth: 0,
      height: 'min(72vh, 680px)',
      maxHeight: 'calc(100dvh - 24px)',
      margin: '12px',
      borderRadius: theme.shape.borderRadius * 2,
    },
  })
}
