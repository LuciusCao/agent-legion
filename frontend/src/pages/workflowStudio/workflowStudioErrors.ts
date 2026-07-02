import type { components } from '../../generated/api'

type CompareError = components['schemas']['WorkflowDraftCompareError']

export type ScopedErrorItem = {
  message: string
  nodeKey?: string
  source?: string
  target?: string
  line?: number | null
  column?: number | null
  onSelectNode?: () => void
}

export type ScopedErrorGroup = {
  category: string
  categoryLabel: string
  items: ScopedErrorItem[]
}

const CATEGORY_LABELS: Record<string, string> = {
  yaml: 'YAML解析',
  schema: '结构校验',
  structure: '结构',
  executor: '执行器绑定',
  revision: '版本',
}

export function parseCompareErrors(
  errors: CompareError[],
  onSelectNode?: (nodeKey: string) => void
): ScopedErrorGroup[] {
  const groups = new Map<string, ScopedErrorItem[]>()
  for (const error of errors) {
    const category = error.category || 'structure'
    const bucket = groups.get(category) ?? []
    bucket.push({
      message: error.message,
      nodeKey: error.node_key ?? undefined,
      source: error.source ?? undefined,
      target: error.target ?? undefined,
      line: error.line,
      column: error.column,
      onSelectNode: error.node_key
        ? () => onSelectNode?.(error.node_key!)
        : undefined,
    })
    groups.set(category, bucket)
  }
  return Array.from(groups.entries()).map(([category, items]) => ({
    category,
    categoryLabel: CATEGORY_LABELS[category] ?? category,
    items,
  }))
}
