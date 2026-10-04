/**
 * #817 方向 a：窄屏右侧抽屉顶边让出页签行。
 * - useStudioMobileNavBottom 量页签行的视口底边（无页签行 / 宽屏
 *   display:none → 0；视口变化重新量）；
 * - studioDrawerPaperStyle 把它写成 paper 上的 CSS 变量；
 * - studioDrawerFloat.module.css 只在 ≤900px 断点消费该变量（宽屏几何
 *   不变）——jsdom 不跑媒体查询，读源钉住规则位置。
 */
import { act, renderHook } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { STUDIO_DRAWER_TOP_INSET_VAR } from './studioDrawerGeometry'
import {
  studioDrawerPaperStyle,
  useStudioMobileNavBottom,
} from './useStudioDrawerPaperStyle'

function mountNavRow(bottom: { value: number }): HTMLElement {
  const row = document.createElement('div')
  row.setAttribute('data-testid', 'studio-mobile-nav-row')
  row.getBoundingClientRect = () =>
    ({ bottom: bottom.value, height: 48, top: bottom.value - 48 }) as DOMRect
  document.body.appendChild(row)
  return row
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('useStudioMobileNavBottom（#817）', () => {
  it('无页签行（或宽屏 display:none 全 0）时为 0：不让位', () => {
    const { result } = renderHook(() => useStudioMobileNavBottom())
    expect(result.current).toBe(0)
  })

  it('量页签行视口底边，视口变化后跟随', () => {
    const bottom = { value: 105 }
    mountNavRow(bottom)
    const { result } = renderHook(() => useStudioMobileNavBottom())
    expect(result.current).toBe(105)
    bottom.value = 128
    act(() => {
      window.dispatchEvent(new Event('resize'))
    })
    expect(result.current).toBe(128)
  })
})

describe('studioDrawerPaperStyle', () => {
  it('写入顶边让位变量 + 栈位 z-index；hidden 才 display:none', () => {
    const style = studioDrawerPaperStyle({
      zIndex: 1201,
      topInset: 105,
      hidden: false,
    }) as Record<string, unknown>
    expect(style.zIndex).toBe(1201)
    expect(style[STUDIO_DRAWER_TOP_INSET_VAR]).toBe('105px')
    expect(style.display).toBeUndefined()
    expect(
      studioDrawerPaperStyle({ zIndex: 1200, topInset: 0, hidden: true })
        .display
    ).toBe('none')
  })

  it('CSS 只在 ≤900px 断点消费让位变量：宽屏 .drawerPaper 不改 top', () => {
    const css = readFileSync(
      resolve(__dirname, 'studioDrawerFloat.module.css'),
      'utf-8'
    )
    const baseRule = css.match(/^\.drawerPaper\s*\{([^}]*)\}/m)
    expect(baseRule).not.toBeNull()
    expect(baseRule![1]).not.toContain(STUDIO_DRAWER_TOP_INSET_VAR)
    expect(baseRule![1]).not.toMatch(/\btop:/)
    const narrow = css.match(
      // 双类特异度（压过 MUI paper 的 emotion top:0 / height:100%）。
      /@media \(max-width: 900px\)\s*\{\s*:global\(\.MuiDrawer-paper\)\.drawerPaper\s*\{([^}]*)\}/
    )
    expect(narrow).not.toBeNull()
    expect(narrow![1]).toContain(`top: var(${STUDIO_DRAWER_TOP_INSET_VAR}`)
    expect(narrow![1]).toContain(`- var(${STUDIO_DRAWER_TOP_INSET_VAR}`)
  })
})
