export function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '网络错误'
}

export function runtimeSourceSyntaxMessage(
  runtimeSourceYaml: string | undefined
): string {
  return runtimeSourceYaml !== undefined
    ? 'agent 建议 YAML 有语法错误，本次未保存任何修改'
    : '草稿 YAML 有语法错误，修复后才能保存运行配置'
}

export function runtimeSaveError(error: unknown): string {
  const status = (error as { status?: number } | null)?.status ?? null
  if (status === 409) {
    return '运行配置保存失败：workflow 刚被其他操作更新，请刷新后重试'
  }
  return `运行配置保存失败：${errorMessage(error)}`
}
