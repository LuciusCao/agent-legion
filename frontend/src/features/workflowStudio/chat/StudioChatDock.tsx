import { AgentPanelDock } from '../../agentPanelDock/AgentPanelDock'
import { StudioChatPanel } from './StudioChatPanel'
import { useStudioState, useStudioView } from '../shared/studioStateContext'
import { useStudioMobileNavHeight } from '../shared/useStudioMobileNavHeight'
import { useAgentPublishRequest } from '../shared/useAgentPublishRequest'
import { useSettingStore } from '../../../stores/settingStore'
import { STUDIO_DRAWER_RIGHT_INSET } from '../shared/studioDrawerGeometry'
import { useStudioNarrowViewport } from '../shared/useStudioNarrowViewport'
import styles from './StudioChatPanel.module.css'

/** Studio 的 Agent 对话 Dock（#795 PR②）：载体从右侧栏迁为 AgentPanelDock
 * ——盖在 DAG 上的非模态浮层（surface "studio-chat"），DAG 区不再被固定右栏
 * 挤占。开合仍走 appbar 开关（#668，StudioViewContext 为唯一状态源）：
 * 关闭按钮 = 收起。hidden（关闭/窄屏未选中 Agent 页签）= **隐藏不卸载**
 * （#797 codex P1：composer 文本与发送队列在子树本地 state、SSE 在 hook
 * 里——卸载即静默丢失，队列里已提交的消息不会再发送）。折叠态已随 #795
 * 收尾移除：开/关两态，Esc = 关闭（同 toggleAgent 路径）。
 * 沿用侧栏时代的两条既有逻辑：应用 agent 的 workflow 草稿前若编辑器有未
 * 发布修改需先确认（否则静默覆盖用户草稿）；#416/#429：agent 发布请求落地
 * （确认/取消/被顶替）后在 Dock 内顶部显示一轮回执（zustand store 共享，
 * 对话框实例写入这里即可见），agent 下一轮工具调用同样能从
 * get_publish_request_status 拿到结果。 */
export function StudioChatDock({ hidden }: { hidden: boolean }) {
  const studio = useStudioState()
  const view = useStudioView()
  const workspaceId = useSettingStore((s) => s.workspaceId) ?? undefined
  // 相同 queryKey 的 useQuery 与 AgentPublishRequestDialog 自动合并；
  // resolvedNotice 来自共享 store：对话框里的确认/取消动作在此同轮可见。
  const { resolvedNotice, clearNotice } = useAgentPublishRequest(workspaceId)
  const mobileNavHeight = useStudioMobileNavHeight()
  const narrow = useStudioNarrowViewport()
  // 轮 9 P2：右侧抽屉（节点详情/共享素材）打开时 Dock 运行时左移避让
  // （不写布局记忆，关抽屉弹回）。窄屏不避让：抽屉在画布列里、Dock 只在
  // Agent 页签可见，两者互斥不共存。
  const drawerOpen = studio.selectedNodeKey !== null || view.materialsOpen
  const rightInset = narrow || !drawerOpen ? 0 : STUDIO_DRAWER_RIGHT_INSET
  return (
    <AgentPanelDock
      surfaceKey="studio-chat"
      title="Agent 助手"
      defaultSize={{ width: 572, height: 704 }}
      // 关闭 = 收起：toggleAgent 是开合的唯一组合出口（#797 codex 复审轮，
      // 窄屏页签同步组合在 useAgentDockOpen 那层）。焦点归还指定顶栏开关
      // （首次关闭、无面板外 focusin 时的稳定恢复目标）。窄屏额外避让
      // 移动端页签导航实测高度（宽屏 nav display:none → 实测 0 天然不加成，
      // 复审轮 6）。
      topInsetExtra={mobileNavHeight}
      rightInset={rightInset}
      restoreFocusSelector='[aria-label="toggle agent panel"]'
      onClose={() => view.toggleAgent()}
      hidden={hidden}
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
      {/* key={workspaceId}（#797 复审批次 P3）：studio 路由参数变化复用
          组件，重挂清空聊天子树的本地 state（已选 agent、composer 未发送
          文本），不把旧 workspace 的残留带进新 workspace。 */}
      <StudioChatPanel
        key={workspaceId ?? 'none'}
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
