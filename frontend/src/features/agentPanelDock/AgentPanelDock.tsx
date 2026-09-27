/**
 * AgentPanelDock（issue #795 PR①）：可拖拽、可缩放的非模态面板容器。
 * - 拖拽/缩放：react-rnd，拖拽把手是标题栏（cancel=button 让标题栏按钮
 *   不发起拖拽）；位置/尺寸/折叠态按 surfaceKey 存 localStorage
 *   （dockPlacement.ts），同 surface 重开恢复记忆位置。
 * - 非模态：Portal + Paper 的自定义 modeless surface（role="dialog" +
 *   aria-modal="false"），不走 MUI Modal——无遮罩、不锁滚动、不圈禁焦点，
 *   面板打开时背后页面全程可滚动可交互（portal 外内容也不进 aria-hidden）。
 * - 折叠：缩成右下角小条，内容区只 display:none 不卸载——面板内容
 *   （如聊天子树的队列与未发送输入）保存在组件本地 state 里，卸载即清空。
 * - z-index 900：高于 AppBar 100/页面内容，低于 Toast 1000、
 *   TokenUsageDialog 1190/1200、MUI Modal 1300（分层契约见 css 模块注释）。
 * - 不遮顶部 AppBar：默认/钳制位置的顶边让开 AppBar **实测**底边
 *   （useAppBarBottom；首帧未测量回退 --app-bar-height 声明值——版本芯片/
 *   放大字体时 AppBar 实测更高，硬编码 56px 会盖住 AppBar 底部）。未被用户
 *   动过（无记忆、未拖拽/缩放）的面板位置是 render 期派生值，实测底边到达
 *   后自动跟随；一旦拖拽/缩放/有记忆即冻结为用户位置。实测值到达与视口
 *   缩小时对记忆/用户几何**重新钳制**（codex P2：兜底 56px 钳的记忆位置
 *   在更高的 AppBar 下会盖住它；窗口缩小后坐标/尺寸可能落出视口）。拖拽
 *   中与提交都按 topInset 钳 y（bounds="window" 允许 y=0，不拦 AppBar）。
 * - 焦点与 a11y（useDockFocus）：打开/展开焦点进面板、折叠焦点到小条、
 *   卸载还原触发元素；Esc 折叠面板（非破坏性，会话保持）——展开期间挂在
 *   document 级（非模态面板失焦后 Esc 仍可用；defaultPrevented 或有全局
 *   Modal/Menu 开着时让给对方，不抢已消费的 Esc）。
 */
import { type ReactNode } from 'react'
import { IconButton, Paper, Portal, Tooltip } from '@mui/material'
import { Close, UnfoldLess } from '@mui/icons-material'
import { Rnd } from 'react-rnd'
import { useAppBarBottom } from '../../hooks/useAppBarBottom'
import {
  APP_BAR_FALLBACK_HEIGHT,
  clampResizeTopInset,
  effectiveMinSize,
} from './dockPlacement'
import { useDockGeometry } from './useDockGeometry'
import { useDockEscape, useDockFocus } from './useDockFocus'
import styles from './AgentPanelDock.module.css'

export interface AgentPanelDockProps {
  /** localStorage 记忆与语义命名的 surface key（如 "customize-preview"）。 */
  surfaceKey: string
  title: string
  onClose: () => void
  children: ReactNode
  /** 无记忆时的初始尺寸（钳制进视口）。 */
  defaultSize?: { width: number; height: number }
  minWidth?: number
  minHeight?: number
  /** 折叠小条文案（默认 `${title}（已折叠，点击展开）`）。 */
  collapsedLabel?: string
}

function readAppBarFallbackHeight(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(
    '--app-bar-height'
  )
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) && parsed > 0
    ? parsed
    : APP_BAR_FALLBACK_HEIGHT
}

export function AgentPanelDock({
  surfaceKey,
  title,
  onClose,
  children,
  defaultSize,
  minWidth = 320,
  minHeight = 240,
  collapsedLabel,
}: AgentPanelDockProps) {
  const appBarBottom = useAppBarBottom()
  const topInset = appBarBottom > 0 ? appBarBottom : readAppBarFallbackHeight()

  // 几何引擎（记忆/默认布局、实测与视口变化重钳、持久化）抽在
  // useDockGeometry（体积预算）；语义见该文件注释。
  const {
    geometry,
    collapsed,
    viewport,
    setGeometryLive,
    commitGeometry,
    setCollapsedPersisted,
  } = useDockGeometry(surfaceKey, topInset, defaultSize)

  const { surfaceRef, chipRef } = useDockFocus(collapsed)

  // Esc 折叠挂在 document 级（非模态面板失焦后 Esc 仍可用；实现与让位
  // 规则见 useDockFocus.ts 的 useDockEscape）。
  useDockEscape(collapsed, () => setCollapsedPersisted(true))

  // 拖拽钳制（codex P2）：bounds="window" 允许 y=0，顶边必须不低于
  // AppBar 实测底边——拖拽中实时钳，提交时同一钳制。
  const clampDragY = (y: number) => Math.max(topInset, y)

  // 有效最小尺寸与几何钳制同约束（codex P2 复审轮）：小视口装不下声明
  // 下限时跟视口走，否则 Rnd 的 minWidth 会把面板撑出小视口。
  const effective = effectiveMinSize(
    minWidth,
    minHeight,
    topInset,
    viewport.width,
    viewport.height
  )

  return (
    <Portal>
      <Rnd
        position={{ x: geometry.x, y: geometry.y }}
        size={{ width: geometry.width, height: geometry.height }}
        minWidth={effective.minWidth}
        minHeight={effective.minHeight}
        bounds="window"
        dragHandleClassName={styles.titleBar}
        cancel="button"
        onDrag={(_event, data) => {
          setGeometryLive({ ...geometry, x: data.x, y: clampDragY(data.y) })
        }}
        onDragStop={(_event, data) => {
          commitGeometry({ ...geometry, x: data.x, y: clampDragY(data.y) })
        }}
        onResize={(_event, _direction, ref, _delta, position) => {
          // 顶部把手缩放同样钳顶边（codex P2 复审轮：拖拽路径已钳，缩放
          // 路径漏了）——高度联动由 clampResizeTopInset 承担（底边不变）。
          const clamped = clampResizeTopInset(
            position,
            ref.offsetHeight,
            topInset
          )
          setGeometryLive({
            x: clamped.x,
            y: clamped.y,
            width: ref.offsetWidth,
            height: clamped.height,
          })
        }}
        onResizeStop={(_event, _direction, ref, _delta, position) => {
          const clamped = clampResizeTopInset(
            position,
            ref.offsetHeight,
            topInset
          )
          commitGeometry({
            x: clamped.x,
            y: clamped.y,
            width: ref.offsetWidth,
            height: clamped.height,
          })
        }}
        style={{
          position: 'fixed',
          zIndex: 900,
          display: collapsed ? 'none' : undefined,
        }}
      >
        <Paper
          ref={surfaceRef}
          role="dialog"
          aria-modal="false"
          aria-label={title}
          tabIndex={-1}
          elevation={8}
          className={styles.surface}
          sx={{
            // 圆角规范（#796 验收反馈）：浮动 chrome 档 8px——Toast 同款，
            // 也是 chat/editor 等浮动表面的主取值；模态对话框档 4px
            // （themeComponents MuiDialog paper）与近全屏档 16px
            // （DagFullscreenDialog）都不适用于可拖拽 Dock。sx 保证压过
            // Paper 默认的 theme.shape.borderRadius（2px），不靠层叠顺序。
            borderRadius: '8px',
          }}
        >
          <div
            className={styles.titleBar}
            data-testid={`dock-${surfaceKey}-handle`}
          >
            <span className={styles.title}>{title}</span>
            <Tooltip title="折叠为右下角小条（内容保持）">
              <IconButton
                size="small"
                aria-label="折叠面板"
                onClick={() => setCollapsedPersisted(true)}
              >
                <UnfoldLess fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="关闭">
              <IconButton size="small" aria-label="关闭" onClick={onClose}>
                <Close fontSize="small" />
              </IconButton>
            </Tooltip>
          </div>
          <div className={styles.content}>{children}</div>
        </Paper>
      </Rnd>
      {collapsed && (
        <button
          ref={chipRef}
          type="button"
          className={styles.chip}
          onClick={() => setCollapsedPersisted(false)}
        >
          {collapsedLabel ?? `${title}（已折叠，点击展开）`}
        </button>
      )}
    </Portal>
  )
}
