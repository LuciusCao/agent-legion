export function runtimeSaveError(error: unknown): string {
  const status = (error as { status?: number } | null)?.status ?? null
  if (status === 409) {
    return '运行配置保存失败：workflow 刚被其他操作更新，请刷新后重试'
  }
  const detail =
    error instanceof Error && error.message ? error.message : '网络错误'
  return `运行配置保存失败：${detail}`
}
