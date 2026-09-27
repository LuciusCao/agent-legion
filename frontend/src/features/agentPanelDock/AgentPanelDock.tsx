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
 *   后自动跟随；一旦拖拽/缩放/有记忆即冻结为用户位置。
 * - 焦点与 a11y（useDockFocus）：打开/展开焦点进面板、折叠焦点到小条、
 *   卸载还原触发元素；Esc 折叠面板（非破坏性，会话保持）。
 */
import { useState, type ReactNode } from 'react'
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
import { useDockFocus } from './useDockFocus'
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
  const geometry =
    geometryOverride ??
    defaultDockGeometry(
      topInset,
      window.innerWidth,
      window.innerHeight,
      defaultSize
    )

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
          setGeometryOverride({ ...geometry, x: data.x, y: data.y })
        }}
        onDragStop={(_event, data) => {
          commitGeometry({ ...geometry, x: data.x, y: data.y })
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
          onKeyDown={(event) => {
            if (event.key === 'Escape') setCollapsedPersisted(true)
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
