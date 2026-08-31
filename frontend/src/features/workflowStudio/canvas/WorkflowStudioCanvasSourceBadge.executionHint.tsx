import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { parseWorkflowExecutionDefaults } from '../shared/workflowStudioYamlDraft.executionDefaults'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'
import { topLevelExecutionMissing } from './workflowStudioExecutionWarnings'

/** #333：顶层 execution 默认缺失的画布整体提示——workflow 含 agent 节点
 * 却没有可回落的顶层 execution 默认（provider/model 皆空）时提醒；节点级
 * 缺口由节点警告徽标（DagNodeHeader）承载，两者互补。草稿 YAML 解析失败
 * 时画布回退 published（其顶层块形状不可得，误报比漏报更吵），不提示。 */
export function WorkflowStudioExecutionHint() {
  const studio = useStudioState()
  const show = useMemo(() => {
    if (
      studio.viewMode === 'draft' &&
      studio.definitionYaml.trim() !== '' &&
      workflowYamlToDefinitionRecord(studio.definitionYaml) === null
    ) {
      return false
    }
    return topLevelExecutionMissing(
      studio.workflow,
      parseWorkflowExecutionDefaults(studio.definitionYaml)
    )
  }, [studio.viewMode, studio.definitionYaml, studio.workflow])
  if (!show) return null
  return (
    <Chip
      size="small"
      color="warning"
      variant="outlined"
      label="未配置顶层 execution 默认，Agent 节点需各自配齐 provider / model"
    />
  )
}
