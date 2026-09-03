import { api } from '../../../api'
import type { components } from '../../../generated/api'

type NodeCodeTemplateResponse =
  components['schemas']['WorkflowNodeCodeTemplateResponse']

// 节点代码以 DB 发布文本为准（workspace 版本或全局出厂种子，#96），
// 不再有 repo 路径。#392 Phase 2：本段的挂载由 nodeTypeSections 注册表
// 保证只在 code 类型渲染，isCodeNode 门控随之退役。
export function fetchNodeCodeTemplate(): Promise<NodeCodeTemplateResponse> {
  return api('/api/workflow-node-code-template')
}
