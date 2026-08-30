import { useState } from 'react'
import { Button } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useSettingStore } from '../../../stores/settingStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import {
  WorkflowNodeAgentEditorPanel,
  agentEditorButtonLabel,
} from './WorkflowNodeAgentEditorPanel'

type Props = {
  /** 已绑定该 capability 的 Agent id；null = 新建模式（capability 预填）。 */
  agentId: string | null
  capability: string
  /** 节点显式执行类型（#284）：type=code 且无 Agent 时入口文案为
   * 「切换为 Agent 执行」，创建成功后由 onSwitchToAgent 改写草稿 YAML。 */
  nodeType?: 'code' | 'agent'
  /** type=code 节点新建 Agent 成功后回调：把草稿 YAML 的节点 type 改为
   * agent（返回是否改写成功）。 */
  onSwitchToAgent?: () => boolean
  readOnly?: boolean
}

/**
 * 节点详情内嵌的 Agent 编辑/新建入口。Agent 定义仍是 workspace 级共享实体
 * （versioned_entities，一 capability 一 published），此处仅改变 UI 承载；
 * 保存/发布/归档后失效 Agent 目录与 Studio capability 路由缓存。
 */
export function WorkflowNodeAgentEditor({
  agentId,
  capability,
  nodeType,
  onSwitchToAgent,
  readOnly,
}: Props) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  if (readOnly || !workspaceId) return null
  const switchToAgent = !agentId && nodeType === 'code'

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
    <div>
      <Button size="small" onClick={() => setOpen((value) => !value)}>
        {agentEditorButtonLabel(open, agentId, switchToAgent)}
      </Button>
      {open && (
        <WorkflowNodeAgentEditorPanel
          workspaceId={workspaceId}
          agentId={agentId}
          capability={capability}
          switchToAgent={switchToAgent}
          onSwitchToAgent={onSwitchToAgent}
          onRefresh={refresh}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}
