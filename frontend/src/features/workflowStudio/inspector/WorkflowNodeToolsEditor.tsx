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
 * #443/#476：agent 节点的节点级 `tools:` 声明编辑入口。选项面是 per-runtime
 * 工具目录；forced 档不可选（激活由节点 outputs 声明驱动）。#575：未声明时
 * 用 helperText 展示解析后的生效值，不留裸空态。#935 / #1079（#440 P3）：
 * 未声明 = 跟随 runtime 的 default 档（与后端 DEFAULT_TOOLS 同源）——Agent
 * 定义不再是兜底来源。
 */
export function WorkflowNodeToolsEditor(props: {
  node: WorkflowNodeRecord
  runtime: string
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  // #575：草稿 parse 与声明值提取合并进一个 memo（输入即 definitionYaml
  // 与 node），不再拆成两个各自 memo 的步骤。
  const declared = useMemo(() => {
    const draft =
      parseWorkflowNode(props.definitionYaml, props.node.key) ?? props.node
    return Array.isArray(draft.tools) ? draft.tools.map(String) : []
  }, [props.definitionYaml, props.node])
  const toolEntries = useRuntimeToolEntries(props.runtime as 'pi' | 'velites')
  const selectableEntries = useMemo(
    () => (toolEntries ?? []).filter((entry) => entry.tier !== 'forced'),
    [toolEntries]
  )
  const invalidTools = useMemo(() => {
    const names = new Set(selectableEntries.map((entry) => entry.name))
    return declared.filter((tool) => !names.has(tool))
  }, [declared, selectableEntries])
  // #575：未声明 = 生效值为 runtime default 档，helperText 直接展示来源与值
  // （目录未加载时不出 hint——未知 ≠ 空）。
  const fallbackTools = toolEntries
    ?.filter((entry) => entry.tier === 'default')
    .map((entry) => entry.name)
  const fallbackName = 'runtime 默认档'
  const fallbackHint =
    declared.length > 0 || fallbackTools === undefined
      ? undefined
      : `当前生效（跟随 ${fallbackName}）：${fallbackTools.join(', ') || '（空）'}`

  return (
    <div className={styles.fieldStack}>
      <TextField
        select
        label={`Tools 覆盖（留空 = 跟随 ${fallbackName}）`}
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
        helperText={fallbackHint}
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
