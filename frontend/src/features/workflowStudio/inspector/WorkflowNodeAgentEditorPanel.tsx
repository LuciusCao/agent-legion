import { useState } from 'react'
import { AgentEditor } from './AgentEditor'

type Props = {
  workspaceId: string
  agentId: string | null
  capability: string
  onRefresh: () => void
  onClose: () => void
}

// 入口按钮文案：收起/编辑/新建。
export function agentEditorButtonLabel(
  open: boolean,
  agentId: string | null
): string {
  if (open) return '收起 Agent 编辑'
  if (agentId) return '编辑 Agent'
  return '为此 capability 新建 Agent'
}

// 内嵌 AgentEditor 的接线层：保存/发布/归档后刷新目录并收起。新建草稿的
// 场景是例外——/api/agent-catalog 只回 published，新建后若直接关面板，
// 用户无法再从这里发布它（发布门禁会挡住 workflow），所以创建后保留新
// Agent ID 并留在面板里切到编辑/发布模式，让「创建草稿 → 发布」在面板
// 内闭环（codex P2 on PR #288；#392 起入口只在 agent 节点上）。
export function WorkflowNodeAgentEditorPanel(props: Props) {
  const [createdAgentId, setCreatedAgentId] = useState<string | null>(null)
  const editingAgentId = props.agentId ?? createdAgentId

  function handleSaved(newAgentId: string) {
    props.onRefresh()
    if (props.agentId) {
      props.onClose()
      return
    }
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
