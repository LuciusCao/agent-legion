import { AgentPanelDock } from '../../agentPanelDock/AgentPanelDock'
import { StudioChatPanel } from './StudioChatPanel'
import { useStudioState, useStudioView } from '../shared/studioStateContext'
import { useAgentPublishRequest } from '../shared/useAgentPublishRequest'
import { useSettingStore } from '../../../stores/settingStore'
import styles from './StudioChatPanel.module.css'

/** Studio 的 Agent 对话 Dock（#795 PR②）：载体从右侧栏迁为 AgentPanelDock
 * ——盖在 DAG 上的非模态浮层（surface "studio-chat"），DAG 区不再被固定右栏
 * 挤占。开合仍走 appbar 开关（#668，StudioViewContext 为唯一状态源）：Dock
 * 的关闭按钮语义等同收起；折叠为右下角小条由 Dock 承担（display:none 不
 * 卸载，会话与 composer 文本保留）。
 * 沿用侧栏时代的两条既有逻辑：应用 agent 的 workflow 草稿前若编辑器有未
 * 发布修改需先确认（否则静默覆盖用户草稿）；#416/#429：agent 发布请求落地
 * （确认/取消/被顶替）后在 Dock 内顶部显示一轮回执（zustand store 共享，
 * 对话框实例写入这里即可见），agent 下一轮工具调用同样能从
 * get_publish_request_status 拿到结果。 */
export function StudioChatDock() {
  const studio = useStudioState()
  const view = useStudioView()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 相同 queryKey 的 useQuery 与 AgentPublishRequestDialog 自动合并；
  // resolvedNotice 来自共享 store：对话框里的确认/取消动作在此同轮可见。
  const { resolvedNotice, clearNotice } = useAgentPublishRequest(workspaceId)
  return (
    <AgentPanelDock
      surfaceKey="studio-chat"
      title="Agent 助手"
      defaultSize={{ width: 520, height: 640 }}
      onClose={() => {
        // 关闭 = 收起（与 appbar 开关同一状态源；只在开着时调用）。
        if (view.agentOpen) view.toggleAgent()
      }}
    >
      {resolvedNotice && (
        <div className={styles.scopeNote} role="status">
          {resolvedNotice}
          <button
            type="button"
            className={styles.scopeNoteDismiss}
            aria-label="关闭发布请求回执"
            onClick={clearNotice}
          >
            ×
          </button>
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
        onSelectNode={studio.requestNodeFocus}
      />
    </AgentPanelDock>
  )
}
