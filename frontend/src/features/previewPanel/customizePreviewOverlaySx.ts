/**
 * 「定制预览」覆盖面板的定位/尺寸契约（#615 方向 A；codex P2-B 后 surface
 * 改为 Portal + Paper 的自定义 modeless 容器，不再经过 MUI Dialog/Modal，
 * Paper 自己承担 fixed 定位）：
 * - 宽屏（≥1200px，MUI lg）：右侧通栏停靠，宽度收敛在 job progress 列
 *   （30vw）内，不遮左栏预览区；顶边对齐 AppBar 底边（--app-bar-height，
 *   单一事实源在 styles.css :root——codex 复审 P2-B：top:0 曾盖住 AppBar
 *   右侧的 Token 用量与任务操作按钮）；
 * - 窄屏（<1200px）：右下浮动卡片降级——dock 宽度已不够聊天可用性，浮动
 *   卡片 + 折叠小条保证左栏仍可露出（底层全程可滚动交互）；卡片高度上限
 *   同样留出 AppBar，不上探遮挡；
 * - 折叠：Paper 缩成右下角小条（内容换成展开按钮 + display:none 隐藏的
 *   聊天子树，组件不卸载、会话保持存活，P2-A）。
 * z-index 分层（仓库实际值，codex 复审 P2 comment 4111446577）：页面内容
 * < AppBar 100 < 本面板 1100 < 全局对话层（TokenUsageDialog backdrop 1190 /
 * panel 1200、MUI Modal 1300）——面板是页面级非模态 chrome，任何全局对话框
 * 打开都必须压在它之上（否则用量面板右侧被盖住、关闭按钮点不到）；同时高于
 * AppBar 与页面内容，保持「盖在 job progress 列上」的本意。
 */
import type { SxProps, Theme } from '@mui/material/styles'

/** Paper 尺寸：折叠为小条 / 宽屏通栏停靠 / 窄屏浮动卡片三态。 */
export function overlaySurfaceSx(collapsed: boolean): SxProps<Theme> {
  return (theme: Theme) =>
    collapsed
      ? {
          position: 'fixed',
          right: 16,
          bottom: 16,
          zIndex: 1100,
          borderRadius: 999,
        }
      : {
          position: 'fixed',
          top: 'var(--app-bar-height, 56px)',
          right: 0,
          bottom: 0,
          zIndex: 1100,
          width: 'calc(30vw - 20px)',
          minWidth: 340,
          borderRadius: 0,
          display: 'flex',
          flexDirection: 'column',
          [theme.breakpoints.down('lg')]: {
            top: 'auto',
            bottom: 12,
            right: 12,
            width: 'min(460px, calc(100vw - 24px))',
            minWidth: 0,
            height: 'min(72vh, 680px)',
            maxHeight: 'calc(100dvh - var(--app-bar-height, 56px) - 24px)',
            borderRadius: theme.shape.borderRadius * 2,
          },
        }
}
