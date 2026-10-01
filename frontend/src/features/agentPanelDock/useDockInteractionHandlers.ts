/**
 * AgentPanelDock 的拖拽/缩放交互回调（#779 终审 P2 从组件拆出保体积预算，
 * 同 useDockGeometry 的拆分模式）。
 *
 * rightInset 的左移避让是渲染期纯偏移（offsetForRightInset），而拖拽/缩放
 * 回调报告的 data.x / position.x 是偏移后的屏幕坐标——直接提交会把临时
 * 避让永久烙进几何与 localStorage（后续渲染再次叠加避让，关抽屉弹不回
 * 原位）。交互起点捕获当时实际应用的偏移量，提交前还原为基础坐标；
 * rightInset=0 或未触发避让时偏移为 0，零影响。必须在起点捕获：进行中
 * 的 live 更新已把屏幕坐标写进 geometry，stop 时刻再算偏移拿不到交互
 * 开始前的值。
 */
import { useRef } from 'react'
import type {
  RndDragCallback,
  RndResizeCallback,
  RndResizeStartCallback,
} from 'react-rnd'
import {
  clampResizeTopInset,
  offsetForRightInset,
  type DockGeometry,
} from './dockPlacement'

export interface DockInteractionHandlersOptions {
  geometry: DockGeometry
  viewport: { width: number; height: number }
  rightInset: number
  topInset: number
  setGeometryLive: (next: DockGeometry) => void
  commitGeometry: (next: DockGeometry) => void
  /** 缩放把手在 Paper 外层包装里（非 Paper 后代，pointerdown/focusin
   * capture 摸不到）——缩放也要抬栈（#801 codex 轮 4 P2）。 */
  raiseOnInteract: () => void
}

export interface DockInteractionHandlers {
  onDragStart: RndDragCallback
  onDrag: RndDragCallback
  onDragStop: RndDragCallback
  onResizeStart: RndResizeStartCallback
  onResize: RndResizeCallback
  onResizeStop: RndResizeCallback
}

export function useDockInteractionHandlers({
  geometry,
  viewport,
  rightInset,
  topInset,
  setGeometryLive,
  commitGeometry,
  raiseOnInteract,
}: DockInteractionHandlersOptions): DockInteractionHandlers {
  // 拖拽钳制（codex P2）：bounds="window" 允许 y=0，顶边必须不低于
  // AppBar 实测底边——拖拽中实时钳，提交时同一钳制。
  const clampDragY = (y: number) => Math.max(topInset, y)

  const interactionOffsetRef = useRef(0)
  const captureInteractionOffset = () => {
    interactionOffsetRef.current =
      offsetForRightInset(geometry, viewport.width, rightInset) - geometry.x
  }

  const onDrag: RndDragCallback = (_event, data) => {
    setGeometryLive({ ...geometry, x: data.x, y: clampDragY(data.y) })
  }
  const onDragStop: RndDragCallback = (_event, data) => {
    commitGeometry({
      ...geometry,
      x: data.x - interactionOffsetRef.current,
      y: clampDragY(data.y),
    })
  }
  const onResizeStart: RndResizeStartCallback = () => {
    raiseOnInteract()
    captureInteractionOffset()
  }
  const onResize: RndResizeCallback = (
    _event,
    _direction,
    ref,
    _delta,
    position
  ) => {
    // 顶部把手缩放同样钳顶边（codex P2 复审轮：拖拽路径已钳，缩放
    // 路径漏了）——高度联动由 clampResizeTopInset 承担（底边不变）。
    const clamped = clampResizeTopInset(position, ref.offsetHeight, topInset)
    setGeometryLive({
      x: clamped.x,
      y: clamped.y,
      width: ref.offsetWidth,
      height: clamped.height,
    })
  }
  const onResizeStop: RndResizeCallback = (
    _event,
    _direction,
    ref,
    _delta,
    position
  ) => {
    const clamped = clampResizeTopInset(position, ref.offsetHeight, topInset)
    commitGeometry({
      x: clamped.x - interactionOffsetRef.current,
      y: clamped.y,
      width: ref.offsetWidth,
      height: clamped.height,
    })
  }

  return {
    onDragStart: captureInteractionOffset,
    onDrag,
    onDragStop,
    onResizeStart,
    onResize,
    onResizeStop,
  }
}
