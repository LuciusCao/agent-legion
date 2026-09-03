import { useEffect } from 'react'

type DraftUnloadGuardOptions = {
  /** 尽力把未落盘编辑立即 PUT；keepalive=true 用于 pagehide（请求可存活于
   * 页面销毁之后）。返回 promise（#429 四轮 P2-1），但本护栏是 fire-and-
   * forget：页面正在离开，没有人能 await 它。 */
  flush: (keepalive: boolean) => void | Promise<void>
  hasUnsavedChanges: () => boolean
}

/** 页面离开防丢：切后台（visibilitychange→hidden）与 pagehide 时立即 flush
 * debounce 窗口内的编辑；beforeunload 在仍有未保存内容（debounce 等待中 /
 * PUT 在途 / 失败未恢复 / GET 失败仅内存）时弹浏览器原生确认，干净的
 * saved/idle 态不打扰。 */
export function useDraftUnloadGuard({
  flush,
  hasUnsavedChanges,
}: DraftUnloadGuardOptions) {
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden' && hasUnsavedChanges()) {
        flush(false)
      }
    }
    const onPageHide = () => {
      if (hasUnsavedChanges()) flush(true)
    }
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges()) return
      event.preventDefault()
      event.returnValue = ''
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pagehide', onPageHide)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('pagehide', onPageHide)
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  }, [flush, hasUnsavedChanges])
}
