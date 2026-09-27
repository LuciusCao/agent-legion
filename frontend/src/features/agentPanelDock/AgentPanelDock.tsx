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
import { useEffect, useState, type ReactNode } from 'react'
import { IconButton, Paper, Portal, Tooltip } from '@mui/material'
import { Close, UnfoldLess } from '@mui/icons-material'
import { Rnd } from 'react-rnd'
import { useAppBarBottom } from '../../hooks/useAppBarBottom'
import {
  APP_BAR_FALLBACK_HEIGHT,
  clampDockGeometry,
  defaultDockGeometry,
  loadDockPlacement,
  saveDockPlacement,
  type DockGeometry,
} from './dockPlacement'
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

  // 用户位置（拖拽/缩放/记忆恢复）为 state；null = 未动过，位置 render 期
  // 派生自默认布局（跟随 AppBar 实测底边，首帧回退声明值）。
  const [geometryOverride, setGeometryOverride] = useState<DockGeometry | null>(
    () => {
      const stored = loadDockPlacement(surfaceKey)
      return stored
        ? clampDockGeometry(
            stored,
            topInset,
            window.innerWidth,
            window.innerHeight
          )
        : null
    }
  )
  const [collapsed, setCollapsed] = useState(
    () => loadDockPlacement(surfaceKey)?.collapsed ?? false
  )
  // 视口尺寸状态化（codex P2：resize 必须触发重渲染，钳制才会跟进）。
  const [viewport, setViewport] = useState(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }))
  useEffect(() => {
    const onResize = () =>
      setViewport({ width: window.innerWidth, height: window.innerHeight })
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const geometry =
    geometryOverride ??
    defaultDockGeometry(topInset, viewport.width, viewport.height, defaultSize)

  // 实测 topInset 到达（兜底→实测）或视口变化时，对已冻结的用户/记忆几何
  // 重新钳制——不钳则高 AppBar 或缩小的窗口会把面板顶边/把手送出去。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 几何钳制是与外部系统（AppBar 实测高度/视口尺寸）同步，合法 effect 用途；函数式更新只在结果变化时落盘
    setGeometryOverride((current) => {
      if (current === null) return null
      const next = clampDockGeometry(
        current,
        topInset,
        viewport.width,
        viewport.height
      )
      return next.x === current.x &&
        next.y === current.y &&
        next.width === current.width &&
        next.height === current.height
        ? current
        : next
    })
  }, [topInset, viewport])

  const { surfaceRef, chipRef } = useDockFocus(collapsed)

  function persist(next: DockGeometry, nextCollapsed: boolean) {
    saveDockPlacement(surfaceKey, { ...next, collapsed: nextCollapsed })
  }

  function commitGeometry(next: DockGeometry) {
    setGeometryOverride(next)
    persist(next, collapsed)
  }

  function setCollapsedPersisted(nextCollapsed: boolean) {
    setCollapsed(nextCollapsed)
    persist(geometry, nextCollapsed)
  }

  // Esc 折叠挂在 document 级（非模态面板失焦后 Esc 仍可用；实现与让位
  // 规则见 useDockFocus.ts 的 useDockEscape）。
  useDockEscape(collapsed, () => setCollapsedPersisted(true))

  // 拖拽钳制（codex P2）：bounds="window" 允许 y=0，顶边必须不低于
  // AppBar 实测底边——拖拽中实时钳，提交时同一钳制。
  const clampDragY = (y: number) => Math.max(topInset, y)

  return (
    <Portal>
      <Rnd
        position={{ x: geometry.x, y: geometry.y }}
        size={{ width: geometry.width, height: geometry.height }}
        minWidth={minWidth}
        minHeight={minHeight}
        bounds="window"
        dragHandleClassName={styles.titleBar}
        cancel="button"
        onDrag={(_event, data) => {
          setGeometryOverride({ ...geometry, x: data.x, y: clampDragY(data.y) })
        }}
        onDragStop={(_event, data) => {
          commitGeometry({ ...geometry, x: data.x, y: clampDragY(data.y) })
        }}
        onResize={(_event, _direction, ref, _delta, position) => {
          setGeometryOverride({
            x: position.x,
            y: position.y,
            width: ref.offsetWidth,
            height: ref.offsetHeight,
          })
        }}
        onResizeStop={(_event, _direction, ref, _delta, position) => {
          commitGeometry({
            x: position.x,
            y: position.y,
            width: ref.offsetWidth,
            height: ref.offsetHeight,
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
