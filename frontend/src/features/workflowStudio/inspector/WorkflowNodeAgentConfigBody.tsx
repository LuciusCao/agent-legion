import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowAgentDefinitionCard } from './WorkflowAgentDefinitionCard'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import { WorkflowNodeSkillEditor } from './WorkflowNodeSkillEditor'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentDefinition: AgentDefinition | undefined
  /** #387：agentDefinition 是 draft-only 回落（该 capability 无 published
   * 版本）时为 true，卡片上方给「未发布」提示。 */
  isDraft?: boolean
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// type=agent 节点的执行能力主体：有 published Agent（或 #387 的 draft-only
// 草稿）时给 skill 绑定（#76，节点级绑定优先于 Agent 定义兜底）+ 执行细节；
// 完全缺失时给指引（发布门禁要求恰好一个 published Agent，会显式报错）。
// #409：编辑态下方内联展开的 Agent 编辑面板已含完整定义信息，只读汇总卡
// 只在 readOnly（历史版本查看，无编辑面板）下渲染，去掉重复的信息层。
export function WorkflowNodeAgentConfigBody(props: Props) {
  if (!props.agentDefinition)
    return (
      <div className={inspectorStyles.empty}>
        该 capability 暂无 published Agent；发布 workflow 前需新建并发布一个。
      </div>
    )
  return (
    <>
      {props.isDraft && (
        <div className={inspectorStyles.empty}>
          草稿 Agent 未发布；发布后才能过 workflow 门禁。
        </div>
      )}
      {props.readOnly && (
        <WorkflowAgentDefinitionCard definition={props.agentDefinition} />
      )}
      <WorkflowNodeSkillEditor
        node={props.node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowAgentExecutionDetails
        node={props.node}
        runtime={props.agentDefinition.runtime}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
