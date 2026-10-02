import { useEffect } from 'react'
import { Z_LAYERS } from '../lib/zLayers'
import { useUiStore } from '../stores/uiStore'
import styles from './Toast.module.css'

export default function Toast() {
  const { toast, clearToast } = useUiStore()

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => {
      clearToast()
    }, 3000)
    return () => clearTimeout(timer)
  }, [toast, clearToast])

  if (!toast) return null

  return (
    <div
      className={`${styles.toast} ${styles[toast.type]}`}
      // 层级取全局刻度（#818）：压过 Studio 右侧抽屉，低于 MUI Modal。
      style={{ zIndex: Z_LAYERS.toast }}
      role="status"
      aria-live="polite"
    >
      {toast.message}
    </div>
  )
}
