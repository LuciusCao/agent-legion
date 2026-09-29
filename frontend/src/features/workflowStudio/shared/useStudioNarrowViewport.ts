import { useEffect, useState } from 'react'

/** ≤900px 窄屏判定（与 WorkflowStudioPageResponsive.module.css 的移动端
 * 断点同值，matchMedia 监听变化）：Dock 浮层不受 data-mobile-panel 的
 * 响应式 CSS 控制，窄屏可见性需要在 JS 侧参与决策（#797 codex P2）。
 * jsdom 无 matchMedia → false（宽屏语义，测试默认不窄）。 */
export function useStudioNarrowViewport(): boolean {
  const [narrow, setNarrow] = useState(
    () =>
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(max-width: 900px)').matches
  )
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia('(max-width: 900px)')
    const onChange = () => setNarrow(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])
  return narrow
}
