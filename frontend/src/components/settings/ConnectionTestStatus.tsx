type ConnectionState = 'idle' | 'testing' | 'success' | 'failed'

export interface ConnectionTestStatusProps {
  state: ConnectionState
  message?: string
}

export function ConnectionTestStatus({
  state,
  message,
}: ConnectionTestStatusProps) {
  if (state === 'idle') return null
  const labels: Record<ConnectionState, string> = {
    idle: '',
    testing: '测试中...',
    success: '连接成功',
    failed: '连接失败',
  }
  const className = `status-badge ${state === 'testing' ? 'running' : state}`
  return (
    <span className={className}>
      {labels[state]}
      {message ? ` · ${message}` : ''}
    </span>
  )
}
