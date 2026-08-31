import { api } from '../../../api'
import type { components } from '../../../generated/api'
import type { WorkflowNodeRecord } from '../../../types'

type NodeCodeTemplateResponse =
  components['schemas']['WorkflowNodeCodeTemplateResponse']

// #284：节点类型由显式 node_type 判定（不再按 capability 反推）；type=code
// 的节点走内置 code 池，节点代码以 DB 发布文本为准（workspace 版本或全局
// 出厂种子），不再有 repo 路径（#96）。
export function isCodeNode(node: WorkflowNodeRecord): boolean {
  return (node.node_type ?? 'code') === 'code'
}

// Backend-owned minimal Node SDK skeleton for the「从模板新建」entry.
export function fetchNodeCodeTemplate(): Promise<NodeCodeTemplateResponse> {
  return api('/api/workflow-node-code-template')
}
