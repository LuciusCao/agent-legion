import type { WorkflowDefinitionRecord } from '../types'

// Job 详情侧的节点目录：从 JobDetail 派生的最小形状，下游（rerun 选择器、
// 节点过滤选项、node-limit 面板）只消费 key/label/capability 级信息。
// 以前 deriveJobDetailPresentation 会为此伪造一个完整的
// WorkflowDefinitionRecord（intake/edges 等字段全是占位）只为满足类型；
// 显式声明真实所需的结构后，类型即文档。
export type NodeCatalogNode = {
  key: string
  label: string
  capability?: string
  // 依赖边：run-to 起始节点校验的 ancestorClosure 沿它回溯。
  after?: string[] | null
  node_type?: string
  terminal?: { outcome?: string } | null
}

export type NodeCatalog = {
  key: string
  label?: string
  nodes: NodeCatalogNode[]
}

// WorkflowDefinitionRecord 结构上即是一个（更丰富的）NodeCatalog：
// 显式声明这层兼容，nodesForJob 等共享工具可以同时接收两者。
export type CatalogSource =
  | NodeCatalog
  | WorkflowDefinitionRecord
  | null
  | undefined

export function catalogNodes(source: CatalogSource): NodeCatalogNode[] {
  return source?.nodes ?? []
}
