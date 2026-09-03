import { StudioChatPanel } from './StudioChatPanel'
import { useStudioState } from '../shared/studioStateContext'
import { useAgentPublishRequest } from '../shared/useAgentPublishRequest'
import { useSettingStore } from '../../../stores/settingStore'
import styles from './StudioChatPanel.module.css'

/** 右半 Agent 对话栏：应用 agent 的 workflow 草稿前，若编辑器有未发布
 * 修改需先确认（否则静默覆盖用户草稿）。#416：agent 发布请求落地
 * （确认/取消）后在栏顶显示一轮回执；agent 下一轮工具调用同样能从
 * get_publish_request_status 拿到结果。 */
export function StudioChatAside({
  agentOpen,
  asideClass,
}: {
  agentOpen: boolean
  asideClass: string
}) {
  const studio = useStudioState()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 相同 queryKey 的 useQuery 与 AgentPublishRequestDialog 自动合并。
  const { resolvedNotice } = useAgentPublishRequest(workspaceId)
  return (
    <aside
      data-mobile-panel="agent"
      data-collapsed={agentOpen ? undefined : 'true'}
      aria-label="Agent 对话面板"
      className={asideClass}
    >
      {resolvedNotice && (
        <div className={styles.scopeNote} role="status">
          {resolvedNotice}
        </div>
      )}
      <StudioChatPanel
        selectedNodeKey={studio.selectedNodeKey}
        definitionYaml={studio.definitionYaml}
        onApplyWorkflowDraft={(yaml) => {
          if (
            studio.dirty &&
            !window.confirm(
              '当前编辑器里有未发布的修改，应用此草稿将覆盖它们。确定继续吗？'
            )
          )
            return
          studio.backToDraft()
          studio.setDefinitionYaml(yaml)
        }}
        onSelectNode={studio.setSelectedNodeKey}
      />
    </aside>
  )
}
