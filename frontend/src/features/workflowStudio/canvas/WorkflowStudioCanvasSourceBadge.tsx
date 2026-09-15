import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { workflowYamlToDefinitionRecord } from './workflowYamlDraftRecord'
import { countNodeChanges } from './workflowStudioDagChanges'
import { WorkflowStudioExecutionHint } from './WorkflowStudioCanvasSourceBadge.executionHint'

/** 画布数据源标识：与顶栏 StatusChip 同源（compare 计数 + dirty）——草稿
 * 有未发布变更时显示计数/变更 chip，无变更（含刚发布完成）不渲染，不再常驻
 * 「草稿（未发布）」（#666）。草稿 YAML 编辑中途非法时画布回退已发布版本，
 * 换警示色说明（不报错、不清空画布）。revision 模式的「只读 vN」标识已在
 * 顶栏 StatusChip，这里不重复。顶层 execution 默认缺失的整体提示由
 * WorkflowStudioExecutionHint 伴随渲染（#333）。 */
export function WorkflowStudioCanvasSourceBadge() {
  const studio = useStudioState()
  const parseFailed = useMemo(
    () =>
      studio.viewMode === 'draft' &&
      studio.definitionYaml.trim() !== '' &&
      workflowYamlToDefinitionRecord(studio.definitionYaml) === null,
    [studio.viewMode, studio.definitionYaml]
  )
  const counts = countNodeChanges(studio.compareSummary)
  return (
    <>
      {studio.viewMode === 'draft' &&
        (parseFailed ? (
          <Chip
            size="small"
            color="warning"
            label="草稿 YAML 未完成解析，画布暂显示已发布版本"
          />
        ) : counts ? (
          <Chip
            size="small"
            variant="outlined"
            color="info"
            label={`草稿（未发布变更 ${counts.total}）`}
          />
        ) : studio.dirty ? (
          <Chip
            size="small"
            variant="outlined"
            color="info"
            label="草稿（有未发布变更）"
          />
        ) : null)}
      <WorkflowStudioExecutionHint />
    </>
  )
}
