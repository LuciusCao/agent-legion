import { useEffect, useState } from 'react'

/**
 * AppBar 实测底边（getBoundingClientRect().bottom）。监听：挂载即量 +
 * window resize（视口变化）+ ResizeObserver 监听 AppBar 元素本身（codex
 * P2 复审轮 #796：异步 pageSubtitle/版本芯片/字体加载把 AppBar 自己撑高时
 * window.resize 不触发，不监听元素则浮层以旧 topInset 覆盖顶部控件）。
 * jsdom 无 ResizeObserver——缺失时退回 mount + window.resize 两条。
 */
export function useAppBarBottom(): number {
  const [bottom, setBottom] = useState(0)
  useEffect(() => {
    const appBar = document.querySelector('[data-testid="app-bar"]')
    const update = () => {
      const bar = document.querySelector('[data-testid="app-bar"]')
      setBottom(bar ? bar.getBoundingClientRect().bottom : 0)
    }
    update()
    window.addEventListener('resize', update)
    const observer =
      typeof ResizeObserver !== 'undefined' && appBar
        ? new ResizeObserver(update)
        : null
    if (observer && appBar) observer.observe(appBar)
    return () => {
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [])
  return bottom
}
