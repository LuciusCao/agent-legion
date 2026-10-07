import { useMemo } from 'react'
import type { WorkflowNodeRecord } from '../../../types'
import {
  readNodeRuntimeInfo,
  type NodeRuntimeInfo,
} from '../shared/workflowStudioYamlDraft.runtime'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import { WorkflowNodeRuntimeSelect } from './WorkflowNodeRuntimeSelect'
import { WorkflowNodeSkillEditor } from './WorkflowNodeSkillEditor'

export type NodeRuntimeView = NodeRuntimeInfo & {
  /** 节点级优先、其次 workflow 顶层默认；'' = legacy（未自含）。 */
  effectiveRuntime: string
}

/** 草稿 YAML 中节点的 runtime 声明（按草稿内容 memo）。 */
export function useNodeRuntimeInfo(
  node: WorkflowNodeRecord,
  definitionYaml: string
): NodeRuntimeView {
  const fallbackRuntime = node.execution?.runtime ?? ''
  return useMemo(() => {
    const info = readNodeRuntimeInfo(definitionYaml, node.key, {
      nodeRuntime: fallbackRuntime,
      defaultRuntime: '',
    })
    return {
      ...info,
      effectiveRuntime: info.nodeRuntime || info.defaultRuntime,
    }
  }, [definitionYaml, node.key, fallbackRuntime])
}

/**
 * #935（#440 P3）：自含 agent 节点（节点或 workflow 顶层声明了
 * `execution.runtime`）的执行档案编辑区。执行档案随 workflow revision
 * 发布、随 job 快照冻结——这里的全部编辑都写进节点草稿 YAML；Agent 定义
 * 不再参与（不渲染 AgentEditor、不提示新建 Agent）。
 */
export function WorkflowNodeProfileBody(props: {
  node: WorkflowNodeRecord
  runtimeInfo: NodeRuntimeView
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  return (
    <>
      <WorkflowNodeRuntimeSelect
        node={props.node}
        nodeRuntime={props.runtimeInfo.nodeRuntime}
        defaultRuntime={props.runtimeInfo.defaultRuntime}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeSkillEditor
        node={props.node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowAgentExecutionDetails
        node={props.node}
        runtime={props.runtimeInfo.effectiveRuntime}
        toolsFallbackSource="runtime"
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
