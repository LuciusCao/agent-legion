import { useUiStore } from '../../../stores/uiStore'
import { AgentEditor } from './AgentEditor'

type Props = {
  workspaceId: string
  agentId: string | null
  capability: string
  /** type=code 节点的新建模式：创建成功后把草稿 YAML 的节点 type 改为
   * agent（改写失败时降级提示手动改 YAML）。 */
  switchToAgent: boolean
  onSwitchToAgent?: () => boolean
  onRefresh: () => void
  onClose: () => void
}

// 入口按钮文案：收起/编辑/切换/新建。
export function agentEditorButtonLabel(
  open: boolean,
  agentId: string | null,
  switchToAgent: boolean
): string {
  if (open) return '收起 Agent 编辑'
  if (agentId) return '编辑 Agent'
  return switchToAgent ? '切换为 Agent 执行' : '为此 capability 新建 Agent'
}

// 内嵌 AgentEditor 的接线层：保存/发布/归档后刷新目录并收起。
export function WorkflowNodeAgentEditorPanel(props: Props) {
  const showToast = useUiStore((s) => s.showToast)

  function handleSaved() {
    props.onRefresh()
    if (props.switchToAgent) {
      const switched = props.onSwitchToAgent?.() ?? false
      showToast(
        switched
          ? '已切换为 Agent 执行，发布 workflow 后生效'
          : 'Agent 草稿已创建；请手动在 YAML 将节点 type 改为 agent 并发布',
        switched ? 'success' : 'error'
      )
    }
    props.onClose()
  }

  return (
    <AgentEditor
      key={props.agentId ?? '__new__'}
      workspaceId={props.workspaceId}
      agentId={props.agentId}
      initialCapability={props.agentId ? undefined : props.capability}
      onSaved={handleSaved}
      onChanged={props.onRefresh}
      onArchived={() => {
        props.onRefresh()
        props.onClose()
      }}
    />
  )
}
