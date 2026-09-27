/**
 * dockPlacement 纯几何助手测试（issue #795 PR①，node 环境纯逻辑）：
 * 默认布局贴右缘且顶边让开 AppBar；clamp 把旧会话的越界几何钳回当前视口。
 * localStorage 读写路径由 AgentPanelDock 组件测试（jsdom）覆盖。
 */
import { describe, it, expect } from 'vitest'
import {
  clampDockGeometry,
  defaultDockGeometry,
  dockStorageKey,
} from './dockPlacement'

describe('defaultDockGeometry', () => {
  it('贴右缘、顶边让开 AppBar 实测底边', () => {
    const g = defaultDockGeometry(56, 1440, 900)
    expect(g).toEqual({ x: 1440 - 520 - 16, y: 64, width: 520, height: 620 })
  })

  it('优先采用 preferred 尺寸并钳制进视口', () => {
    const g = defaultDockGeometry(56, 1440, 900, { width: 1000, height: 640 })
    expect(g).toEqual({ x: 1440 - 1000 - 16, y: 64, width: 1000, height: 640 })
    // preferred 超出视口时收敛（左右/上下各留 16 边距）。
    const small = defaultDockGeometry(56, 800, 600, {
      width: 1000,
      height: 1000,
    })
    expect(small.width).toBe(800 - 32)
    expect(small.height).toBe(600 - 56 - 32)
  })

  it('极小视口高度有下限，不出现负/零高度', () => {
    const g = defaultDockGeometry(56, 1024, 200)
    expect(g.height).toBe(240)
    expect(g.y).toBe(64)
  })
})

describe('clampDockGeometry', () => {
  it('视口内几何原样保留', () => {
    const g = { x: 200, y: 100, width: 520, height: 620 }
    expect(clampDockGeometry(g, 56, 1440, 900)).toEqual(g)
  })

  it('窗口缩小后越界的旧坐标被钳回可视区', () => {
    const g = clampDockGeometry(
      { x: 1300, y: 800, width: 1000, height: 800 },
      56,
      800,
      600
    )
    expect(g.width).toBe(800 - 32)
    expect(g.height).toBe(600 - 56 - 16)
    expect(g.x).toBeLessThanOrEqual(800 - 80)
    expect(g.y).toBeLessThanOrEqual(600 - 80)
    expect(g.y).toBeGreaterThanOrEqual(56)
  })

  it('负坐标（拖出左缘/顶缘）被钳回，且顶边不低于 AppBar', () => {
    const g = clampDockGeometry(
      { x: -500, y: -20, width: 520, height: 620 },
      56,
      1440,
      900
    )
    expect(g.x).toBeGreaterThanOrEqual(16 - 520 + 80)
    expect(g.y).toBe(56)
  })
})

describe('dockStorageKey', () => {
  it('按 surface key 加前缀', () => {
    expect(dockStorageKey('customize-preview')).toBe(
      'agent-panel-dock:customize-preview'
    )
  })
})
