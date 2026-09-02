import { useState } from 'react'
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

// 内嵌 AgentEditor 的接线层：保存/发布/归档后刷新目录并收起。新建路径
// （含「切换为 Agent 执行」）是例外——新建的是 draft-only Agent，若直接
// 关面板，用户无法再从这里发布它（发布门禁会挡住 workflow），所以创建后
// 保留新 Agent ID 并留在面板里切到编辑/发布模式，让「创建草稿 → 发布」在
// 面板内闭环（switchToAgent 先例：codex P2 on PR #288；#387 扩展到普通
// 新建）。
export function WorkflowNodeAgentEditorPanel(props: Props) {
  const showToast = useUiStore((s) => s.showToast)
  const [createdAgentId, setCreatedAgentId] = useState<string | null>(null)
  const editingAgentId = props.agentId ?? createdAgentId

  function handleSaved(newAgentId: string) {
    props.onRefresh()
    if (!props.switchToAgent) {
      // 不另弹 toast：AgentEditor 的「草稿已创建」已可见（双 toast 会互相
      // 顶掉，subagent review P3 on #391），留面板本身就是发布引导。
      setCreatedAgentId(newAgentId)
      return
    }
    const switched = props.onSwitchToAgent?.() ?? false
    showToast(
      switched
        ? '已切换为 Agent 执行，发布 Agent 与 workflow 后生效'
        : 'Agent 草稿已创建；请手动在 YAML 将节点 type 改为 agent 并发布',
      switched ? 'success' : 'error'
    )
    setCreatedAgentId(newAgentId)
  }

  return (
    <AgentEditor
      key={editingAgentId ?? '__new__'}
      workspaceId={props.workspaceId}
      agentId={editingAgentId}
      initialCapability={editingAgentId ? undefined : props.capability}
      onSaved={handleSaved}
      onChanged={props.onRefresh}
      onArchived={() => {
        props.onRefresh()
        props.onClose()
      }}
    />
  )
}
