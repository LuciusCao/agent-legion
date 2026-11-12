/** 左岛发布按钮的禁用原因文案（从 StudioCanvasIslands 拆出保体积预算）。
 * 优先级：草稿冲突 > compare 失败 > 校验失败（结构） > 校验服务不可用
 * （传输） > 未校验。干净态（无未发布变更）不给文案。
 * M48：runtime-only 草稿（校验通过但无结构变更）指引去「运行配置」区。 */
export function studioPublishTooltip(input: {
  dirty: boolean
  inConflict: boolean
  compareError: boolean
  validationMessage: string
  /** M48：草稿相对基线的 runtime 切片差异数（>0 且校验通过但发布禁用
   * = runtime-only 草稿，给运行通道指引）。 */
  runtimeChangedCount?: number
  hasStructuralChanges?: boolean
}): string | undefined {
  if (input.inConflict)
    return '草稿存在冲突，请先在⚠警示中处理（采用 Agent 版本或保留本页编辑）'
  if (input.compareError)
    return '草稿对比失败，点击画布上的「草稿对比失败」警示重试'
  if (!input.dirty) return undefined
  if (input.validationMessage === '校验失败')
    return '校验失败，请修复后重新发布'
  if (input.validationMessage.startsWith('校验失败'))
    return '校验服务暂不可用，稍后编辑即自动重试校验'
  if (input.validationMessage !== '校验通过') return '草稿校验通过后才能发布'
  if ((input.runtimeChangedCount ?? 0) > 0 && !input.hasStructuralChanges)
    return '仅运行配置修改：在「运行配置」区保存即生效，无需发布'
  return undefined
}
