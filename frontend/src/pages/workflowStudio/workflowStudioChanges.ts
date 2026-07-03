import type { components } from '../../generated/api'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']
type CompareSummary = components['schemas']['WorkflowCompareSummary']
type NodeChange = components['schemas']['WorkflowNodeChange']
type EdgeChange = components['schemas']['WorkflowEdgeChange']
type IntakeChange = components['schemas']['WorkflowIntakeChange']
type MetadataChange = components['schemas']['WorkflowMetadataChange']
type RiskFlag = components['schemas']['WorkflowRiskFlag']
type CompareError = components['schemas']['WorkflowDraftCompareError']

export type ChangeSeverity = 'none' | 'info' | 'warning' | 'breaking'

export type NodeChangeGroup = {
  type: NodeChange['type']
  nodeKey: string
  label: string
  fields: string[]
  severity: ChangeSeverity
}

export type EdgeChangeGroup = {
  type: EdgeChange['type']
  source: string
  target: string
  beforeCondition: string | null
  afterCondition: string | null
  severity: ChangeSeverity
}

export type IntakeChangeGroup = {
  type: IntakeChange['type']
  modeKey: string
  fieldKey: string | null
  severity: ChangeSeverity
}

export type MetadataChangeGroup = {
  type: MetadataChange['type']
  field: string
  beforeValue: string | null
  afterValue: string | null
  severity: ChangeSeverity
}

export type RiskFlagGroup = {
  code: string
  message: string
  severity: ChangeSeverity
}

export type ChangeSummaryViewModel = {
  riskLevel: ChangeSeverity
  severityLabel: string
  nodeChanges: NodeChangeGroup[]
  edgeChanges: EdgeChangeGroup[]
  intakeChanges: IntakeChangeGroup[]
  metadataChanges: MetadataChangeGroup[]
  riskFlags: RiskFlagGroup[]
  changedNodeKeys: Set<string>
}

const SEVERITY_ORDER: Record<ChangeSeverity, number> = {
  none: 0,
  info: 1,
  warning: 2,
  breaking: 3,
}

const SEVERITY_META: Record<
  ChangeSeverity,
  { label: string; variant: 'default' | 'info' | 'warning' | 'error' }
> = {
  none: { label: '无风险', variant: 'default' },
  info: { label: '提示', variant: 'info' },
  warning: { label: '警告', variant: 'warning' },
  breaking: { label: '高风险', variant: 'error' },
}

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

export function severityOrder(a: ChangeSeverity, b: ChangeSeverity): number {
  return SEVERITY_ORDER[b] - SEVERITY_ORDER[a]
}

export function severityLabel(severity: ChangeSeverity): string {
  return SEVERITY_META[severity].label
}

export function severityVariant(
  severity: ChangeSeverity
): ChangeSummaryViewModel['riskLevel'] extends ChangeSeverity
  ? 'default' | 'info' | 'warning' | 'error'
  : never {
  return SEVERITY_META[severity].variant
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

function normalizeNodeChange(change: NodeChange): NodeChangeGroup {
  return {
    type: change.type,
    nodeKey: change.node_key,
    label: change.label,
    fields: change.fields,
    severity: change.risk,
  }
}

function normalizeEdgeChange(change: EdgeChange): EdgeChangeGroup {
  return {
    type: change.type,
    source: change.source,
    target: change.target,
    beforeCondition: change.before_condition ?? null,
    afterCondition: change.after_condition ?? null,
    severity: change.risk,
  }
}

function normalizeIntakeChange(change: IntakeChange): IntakeChangeGroup {
  return {
    type: change.type,
    modeKey: change.mode_key,
    fieldKey: change.field_key ?? null,
    severity: change.risk,
  }
}

function normalizeMetadataChange(change: MetadataChange): MetadataChangeGroup {
  return {
    type: change.type,
    field: change.field,
    beforeValue: change.before_value ?? null,
    afterValue: change.after_value ?? null,
    severity: change.risk,
  }
}

function normalizeRiskFlag(flag: RiskFlag): RiskFlagGroup {
  return {
    code: flag.code,
    message: flag.message,
    severity: flag.severity,
  }
}

function collectChangedNodeKeys(
  summary: CompareSummary | null | undefined
): Set<string> {
  const keys = new Set<string>()
  if (!summary) return keys
  for (const change of summary.node_changes) {
    keys.add(change.node_key)
  }
  for (const change of summary.edge_changes) {
    keys.add(change.source)
    keys.add(change.target)
  }
  return keys
}

export function buildChangeSummary(
  response: CompareResponse | null
): ChangeSummaryViewModel {
  if (!response || !response.valid || !response.summary) {
    return {
      riskLevel: 'none',
      severityLabel: SEVERITY_META.none.label,
      nodeChanges: [],
      edgeChanges: [],
      intakeChanges: [],
      metadataChanges: [],
      riskFlags: [],
      changedNodeKeys: new Set(),
    }
  }

  const summary = response.summary
  const nodeChanges = summary.node_changes.map(normalizeNodeChange)
  const edgeChanges = summary.edge_changes.map(normalizeEdgeChange)
  const intakeChanges = summary.intake_changes.map(normalizeIntakeChange)
  const metadataChanges = summary.metadata_changes.map(normalizeMetadataChange)
  const riskFlags = summary.risk_flags
    .map(normalizeRiskFlag)
    .sort((a, b) => severityOrder(a.severity, b.severity))

  return {
    riskLevel: summary.risk_level,
    severityLabel: SEVERITY_META[summary.risk_level].label,
    nodeChanges,
    edgeChanges,
    intakeChanges,
    metadataChanges,
    riskFlags,
    changedNodeKeys: collectChangedNodeKeys(summary),
  }
}

export type ErrorGroup = {
  category: string
  categoryLabel: string
  errors: CompareError[]
}

export function groupCompareErrors(errors: CompareError[]): ErrorGroup[] {
  const groups = new Map<string, CompareError[]>()
  for (const error of errors) {
    const bucket = groups.get(error.category) ?? []
    bucket.push(error)
    groups.set(error.category, bucket)
  }
  return Array.from(groups.entries()).map(([category, items]) => ({
    category,
    categoryLabel: categoryLabelForError(category),
    errors: items,
  }))
}

export function hasBlockingError(errors: CompareError[] | null): boolean {
  if (!errors || errors.length === 0) return false
  return errors.some(
    (error) => error.category === 'yaml' || error.category === 'schema'
  )
}
