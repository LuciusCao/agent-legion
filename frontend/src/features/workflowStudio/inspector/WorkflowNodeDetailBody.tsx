import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { useMemo } from 'react'
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
  /** 预览态（WorkflowNodeDetailDrawer 持有，nodeKey 变化即清除后下发）。 */
  activeKind: NodeDetailPreviewKind | null
  onShowPreview: (kind: NodeDetailPreviewKind) => void
  onClose: () => void
}

/** 详情 panel 内容区：默认节点 inspector；「查看 Prompt / 浏览技能文件」原位
 * 切换为预览视图（不开 dialog，右侧 Agent 对话保持可见可聊）。预览状态由
 * 抽屉持有（面包屑需要感知），本组件只做分发与 context 下发。 */
export function WorkflowNodeDetailBody(props: Props) {
  const { workflow, definitionYaml, compareSummary, nodeKey } = props
  // 与 WorkflowNodeInspector 同一 details 链（含完整草稿转换的形状守卫，
  // #405 codex P1），按输入 memo 化避免每次渲染重解析。
  const details = useMemo(
    () =>
      inspectorNodeDetails(
        { workflow, definitionYaml, compareSummary },
        nodeKey
      ),
    [workflow, definitionYaml, compareSummary, nodeKey]
  )
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
