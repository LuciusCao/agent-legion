import { api } from '../../api'
import type { components } from '../../generated/api'
import type { AgentDefinition } from '../../types/agentCatalogTypes'

type NodeCodeTemplateResponse =
  components['schemas']['WorkflowNodeCodeTemplateResponse']

// P-0.5：无 Agent 路由的节点一律是 code 节点（内置 code 池）；节点代码以
// DB 发布文本为准（workspace 版本或全局出厂种子），不再有 repo 路径（#96）。
export function isCodeNode(
  agentCatalog: AgentDefinition[],
  capability: string
): boolean {
  return !agentCatalog.some(
    (definition) => definition.capability === capability
  )
}

// Backend-owned minimal Node SDK skeleton for the「从模板新建」entry.
export function fetchNodeCodeTemplate(): Promise<NodeCodeTemplateResponse> {
  return api('/api/workflow-node-code-template')
}
