/**
 * AgentPanelDock 的位置/尺寸/折叠态持久化（issue #795 PR①）：按 surface key
 * 存 localStorage（键 `agent-panel-dock:<surfaceKey>`），同 surface 重开面板
 * 时恢复上次拖拽到的位置与折叠态。存储值全部经校验/钳制后才采用——损坏
 * JSON、非数值字段、窗口缩小后越界的旧坐标一律回退默认布局，不信任
 * localStorage 的形状（它是用户可写的输入面）。
 */
export interface DockGeometry {
  x: number
  y: number
  width: number
  height: number
}

export interface DockPlacement extends DockGeometry {
  collapsed: boolean
}

const STORAGE_PREFIX = 'agent-panel-dock:'

/** AppBar 声明 min-height 的兜底值（styles.css :root --app-bar-height 同源）。 */
export const APP_BAR_FALLBACK_HEIGHT = 56

const VIEWPORT_MARGIN = 16
const MIN_VISIBLE = 80

export function dockStorageKey(surfaceKey: string): string {
  return `${STORAGE_PREFIX}${surfaceKey}`
}

/** 默认布局：贴右缘、顶边让开 AppBar（实测底边优先，未测量回退声明值）。 */
export function defaultDockGeometry(
  topInset: number,
  viewportWidth: number,
  viewportHeight: number,
  preferred?: { width: number; height: number }
): DockGeometry {
  const width = Math.min(
    preferred?.width ?? 520,
    viewportWidth - VIEWPORT_MARGIN * 2
  )
  const height = Math.max(
    240,
    Math.min(
      preferred?.height ?? 620,
      viewportHeight - topInset - VIEWPORT_MARGIN * 2
    )
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
    viewportWidth - VIEWPORT_MARGIN * 2
  )
  const height = Math.min(
    Math.max(200, geometry.height),
    viewportHeight - topInset - VIEWPORT_MARGIN
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

export function loadDockPlacement(surfaceKey: string): DockPlacement | null {
  try {
    const raw = window.localStorage.getItem(dockStorageKey(surfaceKey))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<DockPlacement>
    const { x, y, width, height, collapsed } = parsed
    if (
      typeof x !== 'number' ||
      typeof y !== 'number' ||
      typeof width !== 'number' ||
      typeof height !== 'number'
    ) {
      return null
    }
    return { x, y, width, height, collapsed: collapsed === true }
  } catch {
    // 隐私模式/损坏 JSON：静默回退默认布局（不记忆 ≠ 功能不可用）。
    return null
  }
}

export function saveDockPlacement(
  surfaceKey: string,
  placement: DockPlacement
): void {
  try {
    window.localStorage.setItem(
      dockStorageKey(surfaceKey),
      JSON.stringify(placement)
    )
  } catch {
    // 写入失败（配额/隐私模式）：降级为本次会话内记忆，不打断交互。
  }
}
