/**
 * AgentPanelDock（issue #795 PR①）：可拖拽、可缩放的非模态面板容器。
 * - 拖拽/缩放：react-rnd，拖拽把手是标题栏（cancel=button 让标题栏按钮
 *   不发起拖拽）；位置/尺寸按 surfaceKey 存 localStorage
 *   （dockPlacementStorage.ts），同 surface 重开恢复记忆位置。
 * - 非模态：Portal + Paper 的自定义 modeless surface（role="dialog" +
 *   aria-modal="false"），不走 MUI Modal——无遮罩、不锁滚动、不圈禁焦点，
 *   面板打开时背后页面全程可滚动可交互（portal 外内容也不进 aria-hidden）。
 * - 开/关两态（#795 收尾：折叠态移除——有唤起按钮后，开关经各 surface
 *   自己的入口即可，不再需要右下角小条）：标题栏只有关闭按钮，Esc =
 *   关闭（走各 surface 自己的关闭路径：studio 是 hidden、定制预览/job
 *   排查是卸载）；存量 localStorage 里的 collapsed 字段读取时忽略。
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
 * - 焦点与 a11y（useDockFocus）：可见时焦点进面板、卸载/hidden 归还触发
 *   元素；Esc 关闭面板（走 onClose，语义同标题栏关闭）——展开期间挂在
 *   document 级（非模态面板失焦后 Esc 仍可用；defaultPrevented 或有全局
 *   Modal/Menu 开着时让给对方，不抢已消费的 Esc）。同页多实例时 Esc 只关
 *   栈顶：模块级 Dock 栈（dockStack.ts，#801 codex P1），mount/交互置顶、
 *   unmount/hidden 出栈；栈位同时映射视觉层级（z-index = 900 + 栈位，钳
 *   999 低于 Toast 1000）——被点击的 Dock 同步抬到最上层。
 */
import { type ReactNode, useState, useSyncExternalStore } from 'react'
import { IconButton, Paper, Portal, Tooltip } from '@mui/material'
import { Close } from '@mui/icons-material'
import { Rnd } from 'react-rnd'
import { useAppBarBottom } from '../../hooks/useAppBarBottom'
import {
  clampResizeTopInset,
  effectiveMinSize,
  readAppBarFallbackHeight,
} from './dockPlacement'
import { useDockGeometry } from './useDockGeometry'
import { useDockFocus } from './useDockFocus'
import { useDockEscape } from './useDockEscape'
import {
  dockStackRaise,
  dockStackSubscribe,
  dockStackZIndex,
} from './dockStack'
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
  /** 隐藏不卸载（#797 codex P1）：true 时 surface 不渲染，子树保留在不可见
   * 容器——内容组件的本地 state（composer 文本/发送队列）与 hook 级连接
   * （SSE）不因显隐断开。Portal 会逃逸 display:none 祖先，所以隐藏必须由
   * Dock 自身承担（display:none 抑制不换元素类型，子树不重挂）。 */
  hidden?: boolean
  /** 焦点归还的指定目标选择器（#797 复审轮 4，如顶栏开关/头部入口按钮）
   * ——首次关闭、无面板外 focusin 时的稳定恢复目标；归还链：
   * restoreFocusRef → 面板外最后聚焦元素 → 本选择器 → 挂载前元素。 */
  restoreFocusSelector?: string
  /** 顶边额外避让（#797 复审轮 6，如窄屏移动端页签导航高度）——叠加进
   * topInset：默认几何与拖拽钳制都吃它。 */
  topInsetExtra?: number
  /** 显式焦点归还目标（#800 codex P2）：调用方在唤起瞬间记录的触发元素
   * ref，归还链最优先。key 重挂换目标时，旧实例的卸载清理会先把焦点还给
   * 旧触发元素，若仍读挂载时的 document.activeElement 会把它错记成新归还
   * 目标——显式 ref 让归还目标与唤起动作绑定，不吃卸载/挂载的交错顺序。 */
  restoreFocusRef?: { readonly current: HTMLElement | null }
}

export function AgentPanelDock({
  surfaceKey,
  title,
  onClose,
  children,
  defaultSize,
  minWidth = 320,
  minHeight = 240,
  hidden = false,
  restoreFocusSelector,
  topInsetExtra = 0,
  restoreFocusRef,
}: AgentPanelDockProps) {
  const appBarBottom = useAppBarBottom()
  const topInset =
    (appBarBottom > 0 ? appBarBottom : readAppBarFallbackHeight()) +
    topInsetExtra

  // 几何引擎（记忆/默认布局、实测与视口变化重钳、持久化）抽在
  // useDockGeometry（体积预算）；语义见该文件注释。
  const { geometry, viewport, setGeometryLive, commitGeometry } =
    useDockGeometry(surfaceKey, topInset, defaultSize)

  // hidden 时焦点显式还给触发控件（见 useDockFocus）。
  const { surfaceRef } = useDockFocus(
    hidden,
    restoreFocusSelector,
    restoreFocusRef
  )

  // Dock 栈身份（#801 codex P1）：Esc 只关栈顶；栈成员与交互置顶在
  // useDockEscape（入栈/出栈）与这里的 pointerdown/focusin（置顶）。
  // useState 惰性初始化拿稳定 symbol（渲染期读 ref.current 撞 lint 规则）。
  const [stackId] = useState(() => Symbol(`dock:${surfaceKey}`))
  const raiseOnInteract = () => dockStackRaise(stackId)
  // 栈位映射视觉层级（#801 codex 轮 2）：交互抬栈后本面板同步抬到最上
  // 层（z-index = 900 + 栈位，钳 999 低于 Toast 1000）。
  const zIndex = useSyncExternalStore(dockStackSubscribe, () =>
    dockStackZIndex(stackId)
  )

  // Esc 关闭挂在 document 级（非模态面板失焦后 Esc 仍可用；实现与让位
  // 规则见 useDockEscape.ts）。
  useDockEscape(hidden, onClose, stackId)

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
        // 缩放把手在 Paper 外层包装里（非 Paper 后代，pointerdown/focusin
        // capture 摸不到）——缩放也要抬栈（#801 codex 轮 4 P2）。
        onResizeStart={raiseOnInteract}
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
          zIndex,
          // hidden 用 display:none 抑制（不卸载、不换元素类型——子树
          // state/连接全程不断）。
          display: hidden ? 'none' : undefined,
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
          // 交互即置顶（#801 codex P1 栈序）：点击/拖拽/键盘进入都把自己
          // 抬为栈顶，下一次 Esc 只关它。
          onPointerDownCapture={raiseOnInteract}
          onFocusCapture={raiseOnInteract}
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
            <Tooltip title="关闭">
              <IconButton size="small" aria-label="关闭" onClick={onClose}>
                <Close fontSize="small" />
              </IconButton>
            </Tooltip>
          </div>
          <div className={styles.content}>{children}</div>
        </Paper>
      </Rnd>
    </Portal>
  )
}
