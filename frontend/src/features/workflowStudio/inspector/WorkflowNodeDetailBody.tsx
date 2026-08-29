import { useState } from 'react'
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
  onClose: () => void
}

/** 详情 panel 内容区：默认节点 inspector；「查看 Prompt / 浏览技能文件」原位
 * 切换为预览视图（不开 dialog，右侧 Agent 对话保持可见可聊）。预览状态带
 * nodeKey 印记，切换选中节点即自然失效，无需 effect 重置。 */
export function WorkflowNodeDetailBody(props: Props) {
  const [preview, setPreview] = useState<{
    nodeKey: string
    kind: NodeDetailPreviewKind
  } | null>(null)
  const details = inspectorNodeDetails(props, props.nodeKey)
  const activeKind =
    details && preview?.nodeKey === props.nodeKey ? preview.kind : null
  return (
    <NodeDetailPreviewContext.Provider
      value={(kind) => setPreview({ nodeKey: props.nodeKey, kind })}
    >
      {activeKind && details ? (
        <WorkflowNodePreview
          kind={activeKind}
          node={details.node}
          agentCatalog={props.agentCatalog}
          definitionYaml={props.definitionYaml}
          onBack={() => setPreview(null)}
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
