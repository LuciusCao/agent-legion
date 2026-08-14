import {
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  type SelectChangeEvent,
} from '@mui/material'
import { useSettingStore } from '../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { useExecutorCatalog } from './useExecutorCatalog'
import { WorkflowCatalogLoadError } from './WorkflowCatalogLoadError'
import { hasCodeCapability } from './workflowNodeCodeLookup'
import { useStudioNav } from './workflowStudioNav'
import {
  WorkflowExecutorBindingList,
  type CapabilityBinding,
} from './WorkflowExecutorBindingList'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeBindingEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  bindings: CapabilityBinding[]
  executorCatalog: ExecutorDefinition[]
  readOnly?: boolean
}

// 节点 executor 绑定的就地编辑：改 settingStore 草稿后立即整体提交
// （后端 PUT configuration 为整体替换语义，服务端校验失败经 toast 即时报出）。
export function WorkflowNodeBindingEditor(props: Props) {
  const { node } = props
  const workflowKey = useSettingStore((s) => s.settings.workflowKey)
  const catalog = useExecutorCatalog()
  const executorConfiguration = useSettingStore((s) => s.executorConfiguration)
  const isSaving = useSettingStore((s) => s.isSaving)
  const setNodeBinding = useSettingStore((s) => s.setNodeBinding)
  const saveAll = useSettingStore((s) => s.saveAll)
  const nav = useStudioNav()

  const current = executorConfiguration.bindings.find(
    (b) => b.workflow_key === workflowKey && b.node_key === node.key
  )
  const allocatedIds = new Set(
    executorConfiguration.allocations.map((a) => a.executor_id)
  )
  const compatibleExecutors = props.executorCatalog.filter(
    (executor) =>
      allocatedIds.has(executor.id) &&
      executor.capabilities.includes(node.capability)
  )
  const codeBound = hasCodeCapability(props.executorCatalog, node.capability)

  const handleChange = (event: SelectChangeEvent) => {
    const value = event.target.value
    setNodeBinding(workflowKey, node.key, value === '' ? null : value)
    void saveAll()
  }
  // 节点代码区与绑定编辑器同属一个 Inspector；经 aria-label 锚点滚动定位，
  // 避免给已在体积预算上限的 WorkflowNodeCodeSection 增加行数。
  const scrollToCode = () =>
    document
      .querySelector('[aria-label="节点代码"]')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  if (catalog.loadError) {
    return <WorkflowCatalogLoadError onRetry={() => void catalog.retry()} />
  }
  if (props.bindings.length === 0) {
    return (
      <div className={inspectorStyles.empty}>未匹配到 executor capability</div>
    )
  }
  return (
    <>
      <WorkflowExecutorBindingList bindings={props.bindings} />
      {props.readOnly ? (
        <div className={inspectorStyles.empty}>
          {current ? `绑定：${current.executor_id}` : '未绑定 executor'}
        </div>
      ) : (
        <FormControl fullWidth size="small">
          <InputLabel id={`studio-binding-label-${node.key}`}>
            绑定执行器
          </InputLabel>
          <Select
            labelId={`studio-binding-label-${node.key}`}
            label="绑定执行器"
            aria-label={`绑定 ${node.key}`}
            data-testid={`studio-binding-select-${node.key}`}
            value={current?.executor_id ?? ''}
            disabled={isSaving}
            onChange={handleChange}
          >
            <MenuItem value="">未绑定</MenuItem>
            {compatibleExecutors.map((executor) => (
              <MenuItem key={executor.id} value={executor.id}>
                {executor.id} ({executor.kind})
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      {!current && (
        <div className={styles.warning}>未绑定 executor，调度该节点将失败</div>
      )}
      {compatibleExecutors.length === 0 && (
        <div className={styles.warning}>
          没有已分配的执行器支持能力 {node.capability}
        </div>
      )}
      <div className={styles.actions}>
        <Button
          size="small"
          disabled={!current}
          onClick={() => current && nav.openExecutor(current.executor_id)}
        >
          打开 Executor
        </Button>
        {codeBound && (
          <Button size="small" onClick={scrollToCode}>
            查看节点代码
          </Button>
        )}
      </div>
    </>
  )
}
