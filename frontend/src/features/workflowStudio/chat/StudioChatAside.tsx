import { StudioChatPanel } from './StudioChatPanel'
import { useStudioState } from '../shared/studioStateContext'

/** 右半 Agent 对话栏：应用 agent 的 workflow 草稿前，若编辑器有未发布
 * 修改需先确认（否则静默覆盖用户草稿）。 */
export function StudioChatAside({
  agentOpen,
  asideClass,
}: {
  agentOpen: boolean
  asideClass: string
}) {
  const studio = useStudioState()
  return (
    <aside
      data-mobile-panel="agent"
      data-collapsed={agentOpen ? undefined : 'true'}
      aria-label="Agent 对话面板"
      className={asideClass}
    >
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
