import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeRuntimeSelect } from './WorkflowNodeRuntimeSelect'
import inspectorStyles from './WorkflowNodeInspector.module.css'

/**
 * #935（#440 P3）：v93 未能内联的 legacy agent 节点（迁移报告里的未解析
 * 节点）。发布门禁要求先补 `execution.runtime`——只给提示 + runtime 下拉；
 * 选定 runtime 后节点即自含，切换到 WorkflowNodeProfileBody 编辑 skill /
 * tools / 执行参数。#1079（#440 P3b）：旧 Agent 编辑入口（AgentEditor）
 * 与 Agent 定义汇总卡已删除，Agent 定义不再参与节点编辑。
 */
export function WorkflowNodeLegacyAgentBody(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  return (
    <>
      <div className={inspectorStyles.empty}>
        该节点尚未内联执行档案，发布前需补 execution.runtime（在下方选择
        runtime，随后在节点上声明 skill / tools）。
      </div>
      <WorkflowNodeRuntimeSelect
        node={props.node}
        nodeRuntime=""
        defaultRuntime=""
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
