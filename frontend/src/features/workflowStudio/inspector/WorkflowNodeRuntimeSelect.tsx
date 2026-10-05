import { MenuItem, TextField } from '@mui/material'
import type { WorkflowNodeRecord } from '../../../types'
import { patchWorkflowNodeExecution } from '../shared/workflowStudioYamlDraft.execution'
import { useAgentRuntimes } from './useAgentRuntimes'
import styles from './WorkflowStructuredEditor.module.css'

/** runtime 目录未加载时的选项兜底（与后端 AGENT_RUNTIMES 同集合）。 */
const FALLBACK_RUNTIMES = ['velites', 'pi']

/**
 * #935（#440 P3）：agent 节点执行档案的 runtime 选择，写入节点
 * `execution.runtime`。选项来自 runtime 目录（`GET /agent-runtimes`，与
 * dispatch 校验同源）；workflow 顶层声明了默认 runtime 时提供「继承」项
 * （写空 = 删掉节点级覆盖）。
 */
export function WorkflowNodeRuntimeSelect(props: {
  node: WorkflowNodeRecord
  /** 节点级声明值（'' = 未声明）。 */
  nodeRuntime: string
  /** workflow 顶层 execution.runtime 默认（'' = 无）。 */
  defaultRuntime: string
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const { data } = useAgentRuntimes()
  const runtimes = data ? Object.keys(data.runtimes) : FALLBACK_RUNTIMES
  const options =
    runtimes.includes(props.nodeRuntime) || !props.nodeRuntime
      ? runtimes
      : [...runtimes, props.nodeRuntime]
  return (
    <div className={styles.fieldStack}>
      <TextField
        select
        label="Runtime"
        variant="outlined"
        value={props.nodeRuntime}
        onChange={(e) =>
          props.setDefinitionYaml(
            patchWorkflowNodeExecution(
              props.definitionYaml,
              props.node.key,
              'runtime',
              e.target.value
            )
          )
        }
        fullWidth
        disabled={props.readOnly}
        slotProps={{
          inputLabel: { shrink: true },
          select: { displayEmpty: true },
        }}
      >
        {props.defaultRuntime ? (
          <MenuItem value="">
            继承 workflow 默认（{props.defaultRuntime}）
          </MenuItem>
        ) : (
          <MenuItem value="" disabled>
            未设置（发布前必须选择）
          </MenuItem>
        )}
        {options.map((runtime) => (
          <MenuItem key={runtime} value={runtime}>
            {runtime}
          </MenuItem>
        ))}
      </TextField>
    </div>
  )
}
