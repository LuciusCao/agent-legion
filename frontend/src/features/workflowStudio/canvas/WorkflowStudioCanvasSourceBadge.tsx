import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'

/** 画布数据源标识：草稿模式常态显示「草稿（未发布）」；草稿 YAML 编辑中途
 * 非法时画布回退已发布版本，换警示色说明（不报错、不清空画布）。revision
 * 模式的「只读 vN」标识已在顶栏 StatusChip，这里不重复。 */
export function WorkflowStudioCanvasSourceBadge() {
  const studio = useStudioState()
  const parseFailed = useMemo(
    () =>
      studio.viewMode === 'draft' &&
      studio.definitionYaml.trim() !== '' &&
      workflowYamlToDefinitionRecord(studio.definitionYaml) === null,
    [studio.viewMode, studio.definitionYaml]
  )
  if (studio.viewMode !== 'draft') return null
  if (parseFailed) {
    return (
      <Chip
        size="small"
        color="warning"
        label="草稿 YAML 未完成解析，画布暂显示已发布版本"
      />
    )
  }
  return <Chip size="small" variant="outlined" label="草稿（未发布）" />
}
