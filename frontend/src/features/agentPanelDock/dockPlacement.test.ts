/**
 * dockPlacement 纯几何助手测试（issue #795 PR①，node 环境纯逻辑）：
 * 默认布局贴右缘且顶边让开 AppBar；clamp 把旧会话的越界几何钳回当前视口。
 * localStorage 读写路径由 AgentPanelDock 组件测试（jsdom）覆盖。
 */
import { describe, it, expect } from 'vitest'
import { offsetForRightInset } from './dockPlacement'
import {
  clampDockGeometry,
  clampResizeTopInset,
  defaultDockGeometry,
  effectiveMinSize,
} from './dockPlacement'
import { dockStorageKey } from './dockPlacementStorage'

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

  it('矮视口高度封顶于可用空间（#797 复审轮 7：上限优先于 240 下限，底部不出视口）', () => {
    // 200px 高视口：可用 = 200-56-32=112 → 高度 112（y+height=176 ≤ 200）。
    const g = defaultDockGeometry(56, 1024, 200)
    expect(g.height).toBe(112)
    expect(g.y).toBe(64)
    expect(g.y + g.height).toBeLessThanOrEqual(200)
  })

  it('复审批次 P3：退化视口（高 < topInset + 边距）高度钳到 0，不产出负值', () => {
    // 负 height 被 CSS 丢弃后 Rnd 回落 auto，反而把面板撑出视口。
    const g = defaultDockGeometry(400, 640, 320)
    expect(g.height).toBe(0)
    // 宽度同理：视口宽不足双边距时钳到 0。
    expect(defaultDockGeometry(56, 20, 900).width).toBe(0)
  })
})

describe('offsetForRightInset（#804 轮 9 P2：抽屉避让）', () => {
  // 右侧抽屉（节点详情/共享素材，720+8）打开时 Dock 自动左移避让；
  // 抽屉关闭弹回原位（纯运行时偏移，不写布局记忆）。
  it('Dock 右缘越过抽屉左缘 → 左移到不重叠（含 12px 间距）', () => {
    // 视口 1440、抽屉占右 728：抽屉左缘 712；Dock x=1000 w=572 → 右缘 1572 越界。
    expect(offsetForRightInset({ x: 1000, width: 572 }, 1440, 728)).toBe(128)
  })

  it('Dock 已在抽屉左侧（拖开过）→ 不动', () => {
    expect(offsetForRightInset({ x: 100, width: 572 }, 1440, 728)).toBe(100)
  })

  it('rightInset=0（无抽屉）→ 原位', () => {
    expect(offsetForRightInset({ x: 1000, width: 572 }, 1440, 0)).toBe(1000)
  })

  it('避让后贴左缘钳到 8（小视口 + 宽 Dock 不产生负坐标）', () => {
    expect(offsetForRightInset({ x: 800, width: 700 }, 1000, 728)).toBe(8)
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

  it('复审批次 P3：退化视口下钳制的宽高不为负（负尺寸会被 CSS 丢弃、Rnd 回落 auto 出视口）', () => {
    const g = clampDockGeometry(
      { x: 100, y: 100, width: 520, height: 620 },
      400,
      640,
      320
    )
    expect(g.width).toBeGreaterThanOrEqual(0)
    expect(g.height).toBeGreaterThanOrEqual(0)
    expect(g.height).toBe(0)
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

describe('effectiveMinSize', () => {
  it('复审批次 P3：退化视口下 minWidth/minHeight 钳到 0，不为负', () => {
    expect(effectiveMinSize(320, 240, 400, 640, 320)).toEqual({
      minWidth: 320,
      minHeight: 0,
    })
    expect(effectiveMinSize(320, 240, 56, 20, 900).minWidth).toBe(0)
  })
})

describe('dockStorageKey', () => {
  it('按 surface key 加前缀', () => {
    expect(dockStorageKey('customize-preview')).toBe(
      'agent-panel-dock:customize-preview'
    )
  })
})
