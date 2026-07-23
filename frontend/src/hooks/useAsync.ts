import { useEffect, useState } from 'react'

export interface UseAsyncResult<T> {
  data: T | null
  loading: boolean
  error: string
}

export interface UseAsyncOptions {
  /**
   * When false, the task is not started and the hook stays in its initial
   * idle state (data=null, loading=false). Runs begin once it flips to true.
   */
  enabled?: boolean
  /**
   * When true, every re-run restores the initial pending state
   * (data=null, loading=true, error='') before invoking the task.
   */
  resetOnRun?: boolean
}

/**
 * Runs an async task whenever `deps` change and exposes the shared
 * data/loading/error tri-state. Results arriving after unmount or after a
 * newer run started are discarded.
 */
export function useAsync<T>(
  task: () => Promise<T>,
  deps: readonly unknown[],
  options?: UseAsyncOptions
): UseAsyncResult<T> {
  const enabled = options?.enabled ?? true
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    if (options?.resetOnRun) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setData(null)
      setLoading(true)
      setError('')
    }
    Promise.resolve()
      .then(task)
      .then((value) => {
        if (cancelled) return
        setData(value)
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setData(null)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled])

  return { data, loading, error }
}
