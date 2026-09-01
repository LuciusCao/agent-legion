import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { NodeDetailPreviewKind } from './nodeDetailPreviewContext'
import { normalizeNodeSkill } from '../shared/workflowStudioYamlDraft.skill'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import { WorkflowPromptPreviewPanel } from './WorkflowPromptPreviewPanel'
import { WorkflowSkillPreviewPanel } from './WorkflowSkillPreviewPanel'

type Props = {
  kind: NodeDetailPreviewKind
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly: boolean
}

/** 详情 panel 预览视图分发：prompt 走后端预览 API 的编辑型面板（草稿 YAML
 * 实时参与预览，编辑回写草稿），skill 按节点声明的绑定拉取文件列表渲染
 * （#76：节点 skill 优先，capability 绑定的 Agent 技能兜底）。预览入口只存在于
 * type=agent 节点的 Agent 配置卡；skill 查找同样以显式 node_type 为准（#284），
 * code 节点即使 capability 命中 Agent 目录也不展示其技能。 */
export function WorkflowNodePreview(props: Props) {
  const agent =
    props.node.node_type === 'agent'
      ? props.agentCatalog.find(
          (definition) => definition.capability === props.node.capability
        )
      : undefined
  // 区分「草稿里没有这个节点」（回显 published 绑定）与「草稿节点存在但无
  // skill key」（显式清除，不回显 published——codex P2 on PR 317）；agent
  // 兜底不受清除影响（它不是节点绑定）。
  const draftNode = parseWorkflowNode(props.definitionYaml, props.node.key)
  const draftSkill = normalizeNodeSkill(draftNode?.skill)
  const echoSkill = draftNode === undefined ? props.node.skill : null
  const skillKey = draftSkill?.key || echoSkill?.key || agent?.skill || ''
  // 节点绑定 pin 的 ref 作为预览初始查询版本（#76）：草稿节点的绑定优先
  // （草稿 ref 归一后恒非空），草稿没有该节点才回显 published 值。#322：
  // latest = 跟随 HEAD = 不带 ref 的默认详情，不要把它当 tag 传 ?ref=。
  const boundRef = (draftSkill ?? echoSkill)?.ref
  const skillRef = boundRef && boundRef !== 'latest' ? boundRef : undefined
  if (props.kind === 'prompt') {
    return (
      <WorkflowPromptPreviewPanel
        node={props.node}
        fallbackSkillKey={skillKey}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    )
  }
  return <WorkflowSkillPreviewPanel skillKey={skillKey} initialRef={skillRef} />
}
