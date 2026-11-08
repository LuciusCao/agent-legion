import type {
  EdgeChangeGroup,
  IntakeChangeGroup,
  MetadataChangeGroup,
  NodeChangeGroup,
} from './workflowStudioChanges'

import {
  FIELD_LABELS,
  TYPE_LABELS,
} from './workflowStudioChangeFormatterLabels'

export { categoryLabelForError } from './workflowStudioChangeFormatterLabels'

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
