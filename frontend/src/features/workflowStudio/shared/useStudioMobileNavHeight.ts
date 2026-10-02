import { useEffect, useState } from 'react'

const NAV_SELECTOR = '[data-testid="studio-mobile-nav"]'

/** 量谁（target）、观察谁的尺寸变化（observed）、取哪个几何量（read）。
 * 模块级常量：effect 依赖它的身份，挂载后不重挂。 */
export type NavMetric = {
  target: string
  observed: readonly string[]
  read: (rect: DOMRect) => number
}

const NAV_HEIGHT: NavMetric = {
  target: NAV_SELECTOR,
  observed: [NAV_SELECTOR],
  read: (rect) => rect.height,
}

/**
 * 实测移动端页签导航的某个几何量：mount + window.resize + 被观察元素的
 * ResizeObserver（jsdom 无 RO 时退化前两者；测试环境有 RO mock）。宽屏
 * nav display:none → getBoundingClientRect 全 0，天然不加成（无需窄屏分支）。
 */
export function useMeasuredNav(metric: NavMetric): number {
  const [value, setValue] = useState(0)
  useEffect(() => {
    const { target, observed, read } = metric
    const update = () => {
      const el = document.querySelector(target)
      setValue(el ? Math.max(0, read(el.getBoundingClientRect())) : 0)
    }
    update()
    window.addEventListener('resize', update)
    const elements = observed
      .map((selector) => document.querySelector(selector))
      .filter((el): el is Element => el !== null)
    const observer =
      typeof ResizeObserver !== 'undefined' && elements.length > 0
        ? new ResizeObserver(update)
        : null
    for (const el of elements) observer?.observe(el)
    return () => {
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [metric])
  return value
}

/**
 * 移动端页签导航（WorkflowStudioMobileNav）的实测高度（#797 codex 复审轮 6）：
 * 窄屏下 Dock 浮层几乎占满视口宽，默认/钳制顶边只让开 AppBar 会盖住页签
 * （用户没法切页签，只能先关 Dock）——窄屏 Dock 的 topInset 要额外加上
 * nav 高度。
 */
export function useStudioMobileNavHeight(): number {
  return useMeasuredNav(NAV_HEIGHT)
}
