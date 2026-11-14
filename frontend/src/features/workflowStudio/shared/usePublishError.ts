import { useState } from 'react'

export function usePublishError(
  notify: (message: string, type: 'success' | 'error') => void
) {
  const [publishError, setPublishError] = useState<string | null>(null)
  return {
    publishError,
    reportPublishSuccess: (message: string) => {
      setPublishError(null)
      notify(message, 'success')
    },
    reportPublishInvalid: (message: string) => {
      setPublishError(null)
      notify(message, 'error')
    },
    reportPublishError: (error: unknown) => {
      const message = `保存失败：${(error instanceof Error && error.message) || '网络错误'}`
      setPublishError(message)
      notify(message, 'error')
    },
  }
}
