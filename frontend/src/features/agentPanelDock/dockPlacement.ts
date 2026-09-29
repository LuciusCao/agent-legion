/**
 * AgentPanelDock 的位置/尺寸几何计算（issue #795 PR①）：默认布局、视口
 * 钳制、resize 顶边钳制、有效最小尺寸。布局记忆的 localStorage 读写已拆
 * 到 dockPlacementStorage.ts（#797 复审批次，保体积预算）。
 */
export interface DockGeometry {
  x: number
  y: number
  width: number
  height: number
}

/** AppBar 声明 min-height 的兜底值（styles.css :root --app-bar-height 同源）。 */
export const APP_BAR_FALLBACK_HEIGHT = 56

const VIEWPORT_MARGIN = 16
const MIN_VISIBLE = 80

/** 默认布局：贴右缘、顶边让开 AppBar（实测底边优先，未测量回退声明值）。
 * 高度上限 = 视口高 - topInset - 底边距——#797 复审轮 7：先取下限
 * （240）再取上限会导致矮视口（如 320px 高横屏手机 + 页签导航）下面板
 * 底部出视口（composer/发送按钮不可见）；顺序改为下限后上限封顶
 * （上限优先），且与 effectiveMinSize 同一边距约定。 */
export function defaultDockGeometry(
  topInset: number,
  viewportWidth: number,
  viewportHeight: number,
  preferred?: { width: number; height: number }
): DockGeometry {
  const width = Math.min(
    preferred?.width ?? 520,
    // 退化视口（宽/高 < topInset + 边距）钳到 0：负尺寸被 CSS 丢弃后
    // Rnd 回落 auto，反而把面板撑出视口（#797 复审批次 P3）。
    Math.max(0, viewportWidth - VIEWPORT_MARGIN * 2)
  )
  const availableHeight = Math.max(
    0,
    viewportHeight - topInset - VIEWPORT_MARGIN * 2
  )
  const height = Math.min(
    Math.max(240, preferred?.height ?? 620),
    availableHeight
  )
  return {
    x: Math.max(VIEWPORT_MARGIN, viewportWidth - width - VIEWPORT_MARGIN),
    y: topInset + 8,
    width,
    height,
  }
}

/** 把（可能来自旧会话的）几何钳制回当前视口：面板不可被拖出可视区。 */
export function clampDockGeometry(
  geometry: DockGeometry,
  topInset: number,
  viewportWidth: number,
  viewportHeight: number
): DockGeometry {
  const width = Math.min(
    Math.max(240, geometry.width),
    // 退化视口钳到 0（同 defaultDockGeometry，#797 复审批次 P3）。
    Math.max(0, viewportWidth - VIEWPORT_MARGIN * 2)
  )
  const height = Math.min(
    Math.max(200, geometry.height),
    Math.max(0, viewportHeight - topInset - VIEWPORT_MARGIN)
  )
  const x = Math.min(
    Math.max(VIEWPORT_MARGIN - width + MIN_VISIBLE, geometry.x),
    viewportWidth - MIN_VISIBLE
  )
  const y = Math.min(
    Math.max(topInset, geometry.y),
    viewportHeight - MIN_VISIBLE
  )
  return { x, y, width, height }
}

/**
 * 顶部边缘缩放的 topInset 钳制（codex P2 on #796）：上/左上/右上把手把
 * position.y 拉到窗口顶时 react-rnd 不会拦——钳 y 到 topInset，同时把
 * 高度减去钳位移除的量（底边 = y + height 不变，别只钳 y 让面板被拉长
 * 盖过 AppBar 视觉区）。
 */
export function clampResizeTopInset(
  position: { x: number; y: number },
  height: number,
  topInset: number
): { x: number; y: number; height: number } {
  const y = Math.max(topInset, position.y)
  return { x: position.x, y, height: height - (y - position.y) }
}

/**
 * 有效最小尺寸（codex P2 复审轮）：声明下限与几何钳制同约束——小视口
 * （如 320px 宽）装不下声明的 minWidth 时跟视口走，否则 Rnd 的 minWidth/
 * minHeight 会把面板撑出视口（缩放把手/关闭按钮出界）。边距约定与
 * defaultDockGeometry 一致（顶+底各留 VIEWPORT_MARGIN）。
 */
export function effectiveMinSize(
  minWidth: number,
  minHeight: number,
  topInset: number,
  viewportWidth: number,
  viewportHeight: number
): { minWidth: number; minHeight: number } {
  return {
    // 退化视口钳到 0（同 defaultDockGeometry，#797 复审批次 P3）。
    minWidth: Math.max(
      0,
      Math.min(minWidth, viewportWidth - VIEWPORT_MARGIN * 2)
    ),
    minHeight: Math.max(
      0,
      Math.min(minHeight, viewportHeight - topInset - VIEWPORT_MARGIN * 2)
    ),
  }
}
