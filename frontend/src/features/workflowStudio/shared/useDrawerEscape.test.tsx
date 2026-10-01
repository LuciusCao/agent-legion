/**
 * useDrawerEscape 抽屉栈语义测试（#812 codex P2 + 对抗轮 D2/D3，jsdom）：
 * 两个 persistent 抽屉（共享素材 + 节点详情）同时打开时，Esc 只关栈顶
 * （最近打开、视觉最上层）；栈顶关闭后下一击落到新栈顶；有更上层 MUI 模态
 * 在场时两个抽屉都让位。D2：栈位映射 z-index（1200 + 栈位，钳 <1300），
 * Esc 栈序 == 视觉序。D3：suppressed（窄屏非激活面板的隐藏抽屉）不占栈位、
 * 不消费 Esc。模块级栈（drawerStack.ts）跨实例共享——两个 renderHook 实例
 * 复刻生产拓扑。
 */
import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDrawerEscape } from './useDrawerEscape'

function pressEscape() {
  document.dispatchEvent(
    new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    })
  )
}

function renderDrawer(open: boolean, onClose: () => void, suppressed = false) {
  return renderHook(
    ({ open: o, suppressed: s }) => useDrawerEscape(o, onClose, s),
    { initialProps: { open, suppressed } }
  )
}

describe('useDrawerEscape', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('单抽屉 Esc 关闭自己', () => {
    const onClose = vi.fn()
    renderDrawer(true, onClose)
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('两抽屉共存：Esc 只关栈顶（后打开的），栈顶关闭后下一击关另一个', () => {
    // 生产场景复刻：先开共享素材，再从画布选节点开节点详情——旧实现里
    // 先注册的共享素材监听器先 preventDefault，Esc 关掉下层的共享素材、
    // 留下上层的节点详情。栈语义下 Esc 必须先关节点详情。
    const closeMaterials = vi.fn()
    const closeNode = vi.fn()
    const materials = renderDrawer(true, closeMaterials)
    const node = renderDrawer(true, closeNode)

    pressEscape()
    expect(closeNode).toHaveBeenCalledTimes(1)
    expect(closeMaterials).not.toHaveBeenCalled()

    // 节点抽屉随关闭出栈，共享素材成为栈顶。
    node.rerender({ open: false, suppressed: false })
    pressEscape()
    expect(closeMaterials).toHaveBeenCalledTimes(1)
    expect(closeNode).toHaveBeenCalledTimes(1)
    materials.unmount()
    node.unmount()
  })

  it('打开顺序反转时栈顶跟随最近打开者（重新打开即置顶）', () => {
    const closeA = vi.fn()
    const closeB = vi.fn()
    const a = renderDrawer(true, closeA)
    const b = renderDrawer(true, closeB)
    // A 重新打开 → A 置顶，Esc 关 A。
    a.rerender({ open: false, suppressed: false })
    a.rerender({ open: true, suppressed: false })
    pressEscape()
    expect(closeA).toHaveBeenCalledTimes(1)
    expect(closeB).not.toHaveBeenCalled()
    a.unmount()
    b.unmount()
  })

  it('有更上层模态在场时两个抽屉都不消费 Esc', () => {
    const modal = document.createElement('div')
    modal.className = 'MuiModal-root'
    document.body.appendChild(modal)
    const closeMaterials = vi.fn()
    const closeNode = vi.fn()
    const materials = renderDrawer(true, closeMaterials)
    const node = renderDrawer(true, closeNode)

    pressEscape()
    expect(closeMaterials).not.toHaveBeenCalled()
    expect(closeNode).not.toHaveBeenCalled()

    // 模态消失后栈顶恢复消费。
    modal.remove()
    pressEscape()
    expect(closeNode).toHaveBeenCalledTimes(1)
    expect(closeMaterials).not.toHaveBeenCalled()
    materials.unmount()
    node.unmount()
  })

  it('关闭态抽屉不占栈位：Esc 落到仍开着的抽屉', () => {
    const closeA = vi.fn()
    const closeB = vi.fn()
    const a = renderDrawer(false, closeA)
    const b = renderDrawer(true, closeB)
    pressEscape()
    expect(closeB).toHaveBeenCalledTimes(1)
    expect(closeA).not.toHaveBeenCalled()
    a.unmount()
    b.unmount()
  })

  it('D2：栈位映射 z-index（1200 + 栈位），栈顶抽屉在上层', () => {
    const a = renderDrawer(true, vi.fn())
    const b = renderDrawer(true, vi.fn())
    expect(a.result.current).toBe(1200)
    expect(b.result.current).toBe(1201)
    // 栈顶出栈后剩余抽屉回到 1200（订阅驱动跟随）。
    b.rerender({ open: false, suppressed: false })
    expect(a.result.current).toBe(1200)
    // 不在栈内（关闭态）回默认 1200。
    expect(b.result.current).toBe(1200)
    a.unmount()
    b.unmount()
  })

  it('D3：suppressed 的隐藏抽屉不占栈位、不消费 Esc，可见抽屉照常', () => {
    // 窄屏 Agent 页签里节点抽屉打开但所在面板 display:none——它若占着
    // 栈顶，可见的共享素材抽屉的 Esc 会被它挡住。
    const closeVisible = vi.fn()
    const closeHidden = vi.fn()
    const visible = renderDrawer(true, closeVisible)
    const hidden = renderDrawer(true, closeHidden, true)

    pressEscape()
    expect(closeVisible).toHaveBeenCalledTimes(1)
    expect(closeHidden).not.toHaveBeenCalled()
    // 隐藏抽屉不入栈：z-index 保持默认 1200，可见抽屉是栈顶唯一成员。
    expect(visible.result.current).toBe(1200)
    expect(hidden.result.current).toBe(1200)
    visible.unmount()
    hidden.unmount()
  })

  it('D3：suppressed 解除（切回画布页签）即入栈置顶，恢复消费 Esc', () => {
    const closeA = vi.fn()
    const closeB = vi.fn()
    const a = renderDrawer(true, closeA)
    const b = renderDrawer(true, closeB, true)
    // B 解除 suppressed（页签切回画布）→ 入栈即置顶。
    b.rerender({ open: true, suppressed: false })
    pressEscape()
    expect(closeB).toHaveBeenCalledTimes(1)
    expect(closeA).not.toHaveBeenCalled()
    a.unmount()
    b.unmount()
  })
})
