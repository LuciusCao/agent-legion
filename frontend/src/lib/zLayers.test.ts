/**
 * 全局浮层 z-index 刻度契约（#818）：Toast 必须压过 Studio 右侧抽屉的整条
 * 栈位映射带（此前 Toast 1000 < 抽屉 1200，被抽屉横向裁半），同时不能盖过
 * MUI 模态（theme.zIndex.modal）；Dock 带整体低于抽屉与 Toast。栈函数的
 * 钳制上限直接用真实栈验证，防止有人只改刻度常量、栈里又写回魔数。
 */
import { describe, expect, it } from 'vitest'
import {
  dockStackRaise,
  dockStackRemove,
  dockStackZIndex,
} from '../features/agentPanelDock/dockStack'
import {
  drawerStackRaise,
  drawerStackRemove,
  drawerStackZIndex,
} from '../features/workflowStudio/shared/drawerStack'
import { theme } from '../theme'
import { Z_LAYERS } from './zLayers'

describe('Z_LAYERS 全局刻度', () => {
  it('分层单调：Dock 带 < 抽屉带 < Toast < MUI Modal', () => {
    expect(Z_LAYERS.dockBase).toBeLessThanOrEqual(Z_LAYERS.dockMax)
    expect(Z_LAYERS.dockMax).toBeLessThan(Z_LAYERS.studioDrawerBase)
    expect(Z_LAYERS.studioDrawerBase).toBeLessThanOrEqual(
      Z_LAYERS.studioDrawerMax
    )
    expect(Z_LAYERS.studioDrawerMax).toBeLessThan(Z_LAYERS.toast)
    expect(Z_LAYERS.toast).toBeLessThan(theme.zIndex.modal)
    // Tooltip 等更高的 MUI 层不受影响。
    expect(Z_LAYERS.toast).toBeLessThan(theme.zIndex.tooltip)
  })

  it('抽屉栈任意深度都低于 Toast（#818：Toast 不再被抽屉裁掉）', () => {
    const ids = Array.from({ length: 150 }, (_, i) => Symbol(`drawer${i}`))
    for (const id of ids) drawerStackRaise(id)
    expect(drawerStackZIndex(ids[0])).toBe(Z_LAYERS.studioDrawerBase)
    const top = drawerStackZIndex(ids[ids.length - 1])
    expect(top).toBe(Z_LAYERS.studioDrawerMax)
    expect(top).toBeLessThan(Z_LAYERS.toast)
    for (const id of ids) drawerStackRemove(id)
  })

  it('Dock 栈任意深度都低于抽屉带与 Toast', () => {
    const ids = Array.from({ length: 150 }, (_, i) => Symbol(`dock${i}`))
    for (const id of ids) dockStackRaise(id)
    const top = dockStackZIndex(ids[ids.length - 1])
    expect(top).toBe(Z_LAYERS.dockMax)
    expect(top).toBeLessThan(Z_LAYERS.studioDrawerBase)
    for (const id of ids) dockStackRemove(id)
  })
})
