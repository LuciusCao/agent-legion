import { useQueryClient } from '@tanstack/react-query'
import { useSettingStore } from '../../../stores/settingStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { WorkflowNodeAgentEditorPanel } from './WorkflowNodeAgentEditorPanel'

type Props = {
  /** 已绑定该 capability 的 Agent id；null = 新建模式（capability 预填）。 */
  agentId: string | null
  capability: string
  readOnly?: boolean
}

/**
 * type=agent 节点详情内嵌的 Agent 编辑/新建区（#392 起只挂在 agent 节点
 * 上；code 节点的类型变更走头部类型选择器）。Agent 定义仍是 workspace 级
 * 共享实体（versioned_entities，一 capability 一 published），此处仅改变
 * UI 承载；保存/发布/归档后失效 Agent 目录与 Studio capability 路由缓存。
 * #409：去掉「编辑 Agent」开合按钮——Agent 区块直接内联展开编辑面板，
 * 只读/无 workspace 时整块隐藏。
 */
export function WorkflowNodeAgentEditor({
  agentId,
  capability,
  readOnly,
}: Props) {
  const queryClient = useQueryClient()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  if (readOnly || !workspaceId) return null

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    })
    // Agent 发布/归档/回滚改变 capability 路由，Studio 目录同会话失效重取。
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.studioAgentCatalog(workspaceId ?? ''),
    })
  }

  return (
    <WorkflowNodeAgentEditorPanel
      workspaceId={workspaceId}
      agentId={agentId}
      capability={capability}
      onRefresh={refresh}
    />
  )
}
