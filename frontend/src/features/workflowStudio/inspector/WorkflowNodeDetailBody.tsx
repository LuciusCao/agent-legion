import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import {
  NodeDetailPreviewContext,
  type NodeDetailPreviewKind,
} from './nodeDetailPreviewContext'
import { inspectorNodeDetails } from './workflowStudioInspectorDetails'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { WorkflowNodePreview } from './WorkflowNodePreview'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodeKey: string
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly: boolean
  /** 预览态（WorkflowNodeDetailView 持有，带 nodeKey 印记后下发）。 */
  activeKind: NodeDetailPreviewKind | null
  onShowPreview: (kind: NodeDetailPreviewKind) => void
  onClose: () => void
}

/** 详情 panel 内容区：默认节点 inspector；「查看 Prompt / 浏览技能文件」原位
 * 切换为预览视图（不开 dialog，右侧 Agent 对话保持可见可聊）。预览状态由
 * DetailView 持有（面包屑需要感知），本组件只做分发与 context 下发。 */
export function WorkflowNodeDetailBody(props: Props) {
  const details = inspectorNodeDetails(props, props.nodeKey)
  return (
    <NodeDetailPreviewContext.Provider value={props.onShowPreview}>
      {props.activeKind && details ? (
        <WorkflowNodePreview
          kind={props.activeKind}
          node={details.node}
          agentCatalog={props.agentCatalog}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      ) : (
        <WorkflowNodeInspector
          workflow={props.workflow}
          agentCatalog={props.agentCatalog}
          selectedNodeKey={props.nodeKey}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          compareSummary={props.compareSummary}
          readOnly={props.readOnly}
          onClose={props.onClose}
        />
      )}
    </NodeDetailPreviewContext.Provider>
  )
}
