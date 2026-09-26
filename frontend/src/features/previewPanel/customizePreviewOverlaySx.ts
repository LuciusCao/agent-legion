/**
 * 「定制预览」覆盖面板的定位/尺寸契约（#615 方向 A；codex P2-B 后 surface
 * 改为 Portal + Paper 的自定义 modeless 容器，不再经过 MUI Dialog/Modal，
 * Paper 自己承担 fixed 定位）：
 * - 宽屏（≥1200px，MUI lg）：右侧通栏停靠，宽度收敛在 job progress 列
 *   （30vw）内，不遮左栏预览区；顶边跟随 AppBar **实测**底边——测量值经
 *   CSS 变量 --overlay-top-inset 下发（useOverlayChrome，见同目录
 *   overlayChrome.ts），未测量的首帧回退 --app-bar-height（AppBar 声明
 *   min-height 的单一声明点，styles.css :root），不再硬编码 56px
 *   （codex 复审 P2 id 4111642731：版本芯片/放大字体时 AppBar 实测更高）；
 * - 窄屏（<1200px）：右下浮动卡片降级——dock 宽度已不够聊天可用性，浮动
 *   卡片 + 折叠小条保证左栏仍可露出（底层全程可滚动交互）；卡片高度上限
 *   同样吃实测 inset，不上探遮挡 AppBar；
 * - 折叠：Paper 缩成右下角小条（内容换成展开按钮 + display:none 隐藏的
 *   聊天子树，组件不卸载、会话保持存活，P2-A）。
 * z-index 分层（仓库实际值；codex 复审 P2 comments 4111446577 /
 * 4111642734）：页面内容（JobFilterBar 10）< AppBar 100 < 本面板 900 <
 * Toast 1000 = DagFullscreenDialog 1000 < TokenUsageDialog 1190/1200 <
 * MUI Modal 1300——面板是页面级非模态 chrome：高于 AppBar/页面内容以保住
 * 「盖在 job progress 列上」的本意，但必须让位全局通知（底部 toast 不可
 * 被不透明 Paper 压住）与一切全局对话框（用量面板/全屏 DAG/Modal）。
 */
import type { SxProps, Theme } from '@mui/material/styles'

/** 面板顶边：实测 AppBar 底边（--overlay-top-inset）→ 声明值回退。 */
const TOP_INSET = 'var(--overlay-top-inset, var(--app-bar-height, 56px))'

/** Paper 尺寸：折叠为小条 / 宽屏通栏停靠 / 窄屏浮动卡片三态。 */
export function overlaySurfaceSx(collapsed: boolean): SxProps<Theme> {
  return (theme: Theme) =>
    collapsed
      ? {
          position: 'fixed',
          right: 16,
          bottom: 16,
          zIndex: 900,
          borderRadius: 999,
        }
      : {
          position: 'fixed',
          top: TOP_INSET,
          right: 0,
          bottom: 0,
          zIndex: 900,
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
            maxHeight: `calc(100dvh - ${TOP_INSET} - 24px)`,
            borderRadius: theme.shape.borderRadius * 2,
          },
        }
}
