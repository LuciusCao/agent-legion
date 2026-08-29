import type {
  EdgeChangeGroup,
  IntakeChangeGroup,
  MetadataChangeGroup,
  NodeChangeGroup,
} from './workflowStudioChanges'

const TYPE_LABELS: Record<string, string> = {
  added: '新增',
  removed: '删除',
  modified: '修改',
  condition_changed: '条件变更',
  label_changed: '标签变更',
  mode_changed: '模式变更',
  field_added: '新增字段',
  field_removed: '删除字段',
}

const FIELD_LABELS: Record<string, string> = {
  label: '标签',
  capability: '能力',
  inputs: '输入',
  outputs: '输出',
  terminal: '终点',
}

const CATEGORY_LABELS: Record<string, string> = {
  yaml: 'YAML解析',
  schema: '结构校验',
  structure: '结构',
  executor: '执行器',
  revision: '版本',
}

export function categoryLabelForError(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}

export function formatNodeChange(change: NodeChangeGroup): string {
  const typeLabel = TYPE_LABELS[change.type] ?? change.type
  const fieldLabels = change.fields
    .map((field) => FIELD_LABELS[field] ?? field)
    .join('、')
  if (change.type === 'added' || change.type === 'removed') {
    return `${typeLabel}节点 ${change.label || change.nodeKey}`
  }
  return `${change.label || change.nodeKey}: ${fieldLabels}`
}

export function formatEdgeChange(change: EdgeChangeGroup): string {
  const typeLabel = TYPE_LABELS[change.type] ?? change.type
  const before = change.beforeCondition || ''
  const after = change.afterCondition || ''
  if (change.type === 'condition_changed') {
    return `${change.source} → ${change.target}: ${before || '—'} → ${after || '—'}`
  }
  const condition = after || before || ''
  return `${typeLabel}边 ${change.source} → ${change.target}${condition ? ` (${condition})` : ''}`
}

export function formatIntakeChange(change: IntakeChangeGroup): string {
  const typeLabel = TYPE_LABELS[change.type] ?? change.type
  if (change.fieldKey) {
    return `${change.modeKey} / ${change.fieldKey}: ${typeLabel}`
  }
  return `${change.modeKey}: ${typeLabel}`
}

export function formatMetadataChange(change: MetadataChangeGroup): string {
  if (change.field === 'label') {
    return `Workflow 名称: ${change.beforeValue ?? '-'} → ${change.afterValue ?? '-'}`
  }
  return `${change.field}: ${change.beforeValue ?? '-'} → ${change.afterValue ?? '-'}`
}
