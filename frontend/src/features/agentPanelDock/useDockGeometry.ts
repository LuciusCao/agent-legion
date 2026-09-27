/**
 * AgentPanelDock 的几何引擎（#795 PR①；codex P2 复审轮从组件拆出保体积
 * 预算）：记忆/默认布局、视口与 AppBar 实测高度变化时的重新钳制、拖拽/
 * 缩放提交与 localStorage 持久化。
 * - 用户位置（拖拽/缩放/记忆恢复）为 state；null = 未动过，位置 render 期
 *   派生自默认布局（跟随 AppBar 实测底边，首帧回退声明值）。
 * - 实测 topInset 到达（兜底→实测）或视口缩小时对已冻结几何重钳——不钳则
 *   高 AppBar 下记忆位置盖住顶部导航、窗口缩小后把手落出视口（视口尺寸
 *   状态化 + resize 监听，resize 必须触发重渲染钳制才跟进）。
 * - 拖拽/缩放的 topInset 钳制在组件回调里做（onDrag/onDragStop/onResize/
 *   onResizeStop 都钳），这里只管几何状态与存储。
 */
import { useEffect, useState } from 'react'
import {
  clampDockGeometry,
  defaultDockGeometry,
  loadDockPlacement,
  saveDockPlacement,
  type DockGeometry,
} from './dockPlacement'

export interface DockGeometryEngine {
  geometry: DockGeometry
  collapsed: boolean
  /** 状态化的视口尺寸（resize 监听驱动；有效最小尺寸等派生用）。 */
  viewport: { width: number; height: number }
  /** 拖拽/缩放进行中的实时更新（不写存储；提交走 commitGeometry）。 */
  setGeometryLive: (next: DockGeometry) => void
  commitGeometry: (next: DockGeometry) => void
  setCollapsedPersisted: (collapsed: boolean) => void
}

export function useDockGeometry(
  surfaceKey: string,
  topInset: number,
  defaultSize?: { width: number; height: number }
): DockGeometryEngine {
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
  // 视口尺寸状态化：resize 必须触发重渲染，钳制才会跟进。
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
  // 重新钳制；函数式更新只在结果变化时落盘（不打扰在途拖拽）。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 几何钳制是与外部系统（AppBar 实测高度/视口尺寸）同步，合法 effect 用途
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

  return {
    geometry,
    collapsed,
    viewport,
    setGeometryLive: setGeometryOverride,
    commitGeometry,
    setCollapsedPersisted,
  }
}
