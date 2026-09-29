/**
 * AgentPanelDock 布局记忆的 localStorage 读写（#797 复审批次从
 * dockPlacement.ts 拆出保体积预算；几何计算留在 dockPlacement.ts）。
 * 按 surface key 存（键 `agent-panel-dock:<surfaceKey>`），同 surface 重开
 * 面板时恢复上次拖拽到的位置。存储值全部经校验/钳制后才采用——损坏
 * JSON、非数值字段、窗口缩小后越界的旧坐标一律回退默认布局，不信任
 * localStorage 的形状（它是用户可写的输入面）。
 * 折叠态已随 #795 收尾移除：存量数据里的 collapsed 字段读取时直接忽略
 * （当作没存过折叠，不崩溃、不复活 chip）。
 */
import type { DockGeometry } from './dockPlacement'

const STORAGE_PREFIX = 'agent-panel-dock:'

export function dockStorageKey(surfaceKey: string): string {
  return `${STORAGE_PREFIX}${surfaceKey}`
}

export function loadDockPlacement(surfaceKey: string): DockGeometry | null {
  try {
    const raw = window.localStorage.getItem(dockStorageKey(surfaceKey))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<DockGeometry>
    const { x, y, width, height } = parsed
    if (
      typeof x !== 'number' ||
      typeof y !== 'number' ||
      typeof width !== 'number' ||
      typeof height !== 'number'
    ) {
      return null
    }
    return { x, y, width, height }
  } catch {
    // 隐私模式/损坏 JSON：静默回退默认布局（不记忆 ≠ 功能不可用）。
    return null
  }
}

export function saveDockPlacement(
  surfaceKey: string,
  geometry: DockGeometry
): void {
  try {
    window.localStorage.setItem(
      dockStorageKey(surfaceKey),
      JSON.stringify(geometry)
    )
  } catch {
    // 写入失败（配额/隐私模式）：降级为本次会话内记忆，不打断交互。
  }
}
