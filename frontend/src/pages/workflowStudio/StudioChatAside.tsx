import { StudioChatPanel } from './chat/StudioChatPanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'

/** 右半 Agent 对话栏：应用 agent 的 workflow 草稿前，若编辑器有未发布
 * 修改需先确认（否则静默覆盖用户草稿）。 */
export function StudioChatAside({
  props,
  agentOpen,
  asideClass,
}: {
  props: StudioLayoutProps
  agentOpen: boolean
  asideClass: string
}) {
  return (
    <aside
      data-mobile-panel="agent"
      data-collapsed={agentOpen ? undefined : 'true'}
      aria-label="Agent 对话面板"
      className={asideClass}
    >
      <StudioChatPanel
        selectedNodeKey={props.selectedNodeKey}
        definitionYaml={props.definitionYaml}
        onApplyWorkflowDraft={(yaml) => {
          if (
            props.dirty &&
            !window.confirm(
              '当前编辑器里有未发布的修改，应用此草稿将覆盖它们。确定继续吗？'
            )
          )
            return
          props.backToDraft()
          props.setDefinitionYaml(yaml)
        }}
        onSelectNode={props.setSelectedNodeKey}
      />
    </aside>
  )
}
