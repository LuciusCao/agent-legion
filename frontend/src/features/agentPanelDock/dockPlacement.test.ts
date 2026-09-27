/**
 * dockPlacement 纯几何助手测试（issue #795 PR①，node 环境纯逻辑）：
 * 默认布局贴右缘且顶边让开 AppBar；clamp 把旧会话的越界几何钳回当前视口。
 * localStorage 读写路径由 AgentPanelDock 组件测试（jsdom）覆盖。
 */
import { describe, it, expect } from 'vitest'
import {
  clampDockGeometry,
  clampResizeTopInset,
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

describe('clampResizeTopInset', () => {
  it('y 低于 topInset：钳 y 且高度减去钳位量（底边不变）', () => {
    // 顶部把手拖到窗口顶外：y=-500、height=1184（底边 684）。
    const r = clampResizeTopInset({ x: 300, y: -500 }, 1184, 56)
    expect(r).toEqual({ x: 300, y: 56, height: 628 })
    expect(r.y + r.height).toBe(-500 + 1184)
  })

  it('y 合法时原样返回（高度不动）', () => {
    expect(clampResizeTopInset({ x: 300, y: 100 }, 620, 56)).toEqual({
      x: 300,
      y: 100,
      height: 620,
    })
  })

  it('恰好等于 topInset 时不钳', () => {
    expect(clampResizeTopInset({ x: 0, y: 56 }, 400, 56)).toEqual({
      x: 0,
      y: 56,
      height: 400,
    })
  })
})

describe('dockStorageKey', () => {
  it('按 surface key 加前缀', () => {
    expect(dockStorageKey('customize-preview')).toBe(
      'agent-panel-dock:customize-preview'
    )
  })
})
