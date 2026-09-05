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
  mode_changed: '模式变更',
  field_added: '新增字段',
  field_removed: '删除字段',
  reordered: '顺序调整',
}

const FIELD_LABELS: Record<string, string> = {
  label: '标签',
  capability: '能力',
  inputs: '输入',
  outputs: '输出',
  terminal: '终点',
  // #418：compare 已补 config/config_schema 比对（风险档 warning），
  // 展示层同步给中文标签，避免变更列表里裸字段名。
  config: '节点配置值',
  config_schema: '配置 Schema',
  execution: '执行配置',
  skill: 'Skill 绑定',
  accepted_item_types: '入口条目类型',
  node_type: '节点类型',
  after: '依赖顺序',
  shard: '分片',
  reduce: '聚合',
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
  if (change.type === 'reordered') {
    return '边顺序调整（边集合不变）'
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
