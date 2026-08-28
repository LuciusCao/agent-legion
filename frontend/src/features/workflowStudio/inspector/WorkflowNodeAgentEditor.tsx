import { useState } from 'react'
import { Button } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useSettingStore } from '../../../stores/settingStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { AgentEditor } from './AgentEditor'

type Props = {
  /** 已绑定该 capability 的 Agent id；null = 新建模式（capability 预填）。 */
  agentId: string | null
  capability: string
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
  readOnly,
}: Props) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  if (readOnly || !workspaceId) return null

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    })
    // Agent 发布/归档/回滚改变 capability 路由，Studio 目录同会话失效重取。
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.studioExecutorCatalog(workspaceId ?? ''),
    })
  }

  return (
    <div>
      <Button size="small" onClick={() => setOpen((value) => !value)}>
        {open
          ? '收起 Agent 编辑'
          : agentId
            ? '编辑 Agent'
            : '为此 capability 新建 Agent'}
      </Button>
      {open && (
        <AgentEditor
          key={agentId ?? '__new__'}
          workspaceId={workspaceId}
          agentId={agentId}
          initialCapability={agentId ? undefined : capability}
          onSaved={() => {
            refresh()
            setOpen(false)
          }}
          onChanged={refresh}
          onArchived={() => {
            refresh()
            setOpen(false)
          }}
        />
      )}
    </div>
  )
}
