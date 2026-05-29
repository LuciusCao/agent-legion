import { useEffect } from 'react'
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
      role="status"
      aria-live="polite"
    >
      {toast.message}
    </div>
  )
}
