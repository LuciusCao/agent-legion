import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'

/** 画布数据源标识：草稿 YAML 编辑中途非法时画布回退已发布版本，换警示
 * 色说明（不报错、不清空画布）。「草稿（未发布变更 N）」chip 随 #804
 * 定案退役——未发布变更/校验状态由左岛状态 chip 唯一承接；revision 模式
 * 的「只读 vN」标识亦在左岛 chip。顶层 execution 缺失的画布级提示随
 * #804 抽屉化退役（#333）：缺口由节点徽标（DagNodeHeader 的 execution
 * 缺失标记）承载。 */
export function WorkflowStudioCanvasSourceBadge() {
  const studio = useStudioState()
  const parseFailed = useMemo(
    () =>
      studio.viewMode === 'draft' &&
      studio.definitionYaml.trim() !== '' &&
      workflowYamlToDefinitionRecord(studio.definitionYaml) === null,
    [studio.viewMode, studio.definitionYaml]
  )
  if (studio.viewMode !== 'draft' || !parseFailed) return null
  return (
    <Chip
      size="small"
      color="warning"
      label="草稿 YAML 未完成解析，画布暂显示已发布版本"
    />
  )
}
