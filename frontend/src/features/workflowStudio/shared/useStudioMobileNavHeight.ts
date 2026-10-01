import { useEffect, useState } from 'react'

/**
 * 移动端页签导航（WorkflowStudioMobileNav）的实测高度（#797 codex 复审轮 6）：
 * 窄屏下 Dock 浮层几乎占满视口宽，默认/钳制顶边只让开 AppBar 会盖住页签
 * （用户没法切页签，只能先关 Dock）——窄屏 Dock 的 topInset 要额外加上
 * nav 高度。宽屏 nav display:none → 实测高度 0，天然不加成（无需窄屏分支）。
 * 监听方式与 useAppBarBottom 同款：mount + window.resize + 元素
 * ResizeObserver（jsdom 无 RO 时退化前两者；测试环境有 RO mock）。
 */
export function useStudioMobileNavHeight(): number {
  const [height, setHeight] = useState(0)
  useEffect(() => {
    const update = () => {
      const nav = document.querySelector('[data-testid="studio-mobile-nav"]')
      setHeight(nav ? nav.getBoundingClientRect().height : 0)
    }
    update()
    window.addEventListener('resize', update)
    const nav = document.querySelector('[data-testid="studio-mobile-nav"]')
    const observer =
      typeof ResizeObserver !== 'undefined' && nav
        ? new ResizeObserver(update)
        : null
    if (observer && nav) observer.observe(nav)
    return () => {
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [])
  return height
}
