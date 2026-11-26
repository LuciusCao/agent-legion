const statusOf = (error: unknown) =>
  (error as { status?: number } | null | undefined)?.status

export function sharedResourceErrorMessage(
  error: unknown,
  fallback = '操作没有完成，请稍后重试。'
): string {
  const status = statusOf(error)
  if (status === 403 || status === 404) {
    return '你没有权限执行这个操作，或目标已经不存在。'
  }
  if (status === 409) {
    const message = error instanceof Error ? error.message : ''
    if (message.includes('revision conflict')) {
      return '当前版本刚被其他人更改。页面已刷新，请确认后再试。'
    }
    if (message.includes('still has references')) {
      return '这个版本仍被工作区或任务使用，不能删除。已有任务会继续保留当时使用的版本。'
    }
    if (message.includes('revoked')) {
      return '这个资源已停止用于新任务。已有任务不受影响。'
    }
    return '数据刚发生变化，请刷新后再试。'
  }
  if (status === 422) {
    return '来源文件已变化或暂时不可用，请重新选择来源后再试。'
  }
  if (status === 503) {
    return '资源存储暂时不可用，请稍后再试。'
  }
  return error instanceof Error &&
    error.message &&
    !error.message.startsWith('HTTP ')
    ? error.message
    : fallback
}

export function isResourceAccessError(error: unknown): boolean {
  const status = statusOf(error)
  return status === 403 || status === 404
}
