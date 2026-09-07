import { Chip, TextField, MenuItem } from '@mui/material'
import { useMemo } from 'react'
import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeTools,
} from '../shared/workflowStudioYamlDraft'
import { useRuntimeToolEntries } from './useAgentRuntimes'
import styles from './WorkflowStructuredEditor.module.css'

/**
 * #443/#476：agent 节点的节点级 `tools:` 声明编辑入口。选项面与
 * AgentEditor 同源（per-runtime 工具目录）；空 = 未声明（dispatch 回落
 * Agent 定义的 tools）。forced 档不可选（激活由节点 outputs 声明驱动）。
 */
export function WorkflowNodeToolsEditor(props: {
  node: WorkflowNodeRecord
  runtime: string
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const draft =
    parseWorkflowNode(props.definitionYaml, props.node.key) ?? props.node
  const declared = useMemo(
    () => (Array.isArray(draft.tools) ? draft.tools.map(String) : []),
    [draft.tools]
  )
  const toolEntries = useRuntimeToolEntries(props.runtime as 'pi' | 'velites')
  const selectableEntries = useMemo(
    () => (toolEntries ?? []).filter((entry) => entry.tier !== 'forced'),
    [toolEntries]
  )
  const selectableNames = useMemo(
    () => new Set(selectableEntries.map((entry) => entry.name)),
    [selectableEntries]
  )
  const invalidTools = useMemo(
    () => declared.filter((tool) => !selectableNames.has(tool)),
    [declared, selectableNames]
  )

  return (
    <div className={styles.fieldStack}>
      <TextField
        select
        label="工具声明（空 = 跟随 Agent 定义）"
        variant="outlined"
        value={declared}
        onChange={(e) => {
          const next = e.target.value
          const tools = typeof next === 'string' ? next.split(',') : next
          props.setDefinitionYaml(
            patchWorkflowNodeTools(props.definitionYaml, props.node.key, tools)
          )
        }}
        fullWidth
        disabled={props.readOnly || !toolEntries}
        slotProps={{ select: { multiple: true } }}
      >
        {selectableEntries.map((entry) => (
          <MenuItem key={entry.name} value={entry.name}>
            {entry.name}
            {entry.tier === 'opt-in' ? '（可选开启）' : ''}
          </MenuItem>
        ))}
      </TextField>
      {/* codex P2 on #527：失效项渲染为可点的移除 chip——多选下拉无法
          取消禁用项，留剔除去处（原先只提示「请剔除」却无入口）。 */}
      {invalidTools.length > 0 && (
        <div className={styles.fieldHint} role="alert">
          已声明工具不在 runtime {props.runtime} 的目录里——dispatch
          会拒绝，请移除：
          {invalidTools.map((tool) => (
            <Chip
              key={tool}
              size="small"
              color="error"
              label={tool}
              onDelete={() =>
                props.setDefinitionYaml(
                  patchWorkflowNodeTools(
                    props.definitionYaml,
                    props.node.key,
                    declared.filter((selected) => selected !== tool)
                  )
                )
              }
              sx={{ marginLeft: 1 }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
