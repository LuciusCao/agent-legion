import { describe, expect, it } from 'vitest'
import {
  buildChangeSummary,
  categoryLabelForError,
  formatEdgeChange,
  formatNodeChange,
  severityOrder,
} from './workflowStudioChanges'
import type { components } from '../../generated/api'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']
type NodeChange = components['schemas']['WorkflowNodeChange']
type EdgeChange = components['schemas']['WorkflowEdgeChange']
type RiskFlag = components['schemas']['WorkflowRiskFlag']

function makeResponse(
  overrides: Partial<CompareResponse> = {}
): CompareResponse {
  return {
    valid: true,
    creates_revision: false,
    base_revision: null,
    draft_workflow: null,
    summary: {
      risk_level: 'none',
      node_changes: [],
      edge_changes: [],
      intake_changes: [],
      metadata_changes: [],
      risk_flags: [],
    },
    errors: [],
    ...overrides,
  }
}

function makeNodeChange(overrides: Partial<NodeChange> = {}): NodeChange {
  return {
    type: 'modified',
    node_key: 'node_a',
    label: '节点 A',
    node_type: 'node',
    fields: ['label'],
    risk: 'info',
    ...overrides,
  }
}

function makeEdgeChange(overrides: Partial<EdgeChange> = {}): EdgeChange {
  return {
    type: 'condition_changed',
    source: 'source_a',
    target: 'target_a',
    before_condition: '$.eligible == true',
    after_condition: '$.eligible === true',
    risk: 'breaking',
    ...overrides,
  }
}

function makeRiskFlag(overrides: Partial<RiskFlag> = {}): RiskFlag {
  return {
    code: 'risk_a',
    message: 'Risk A',
    severity: 'info',
    ...overrides,
  }
}

describe('workflowStudioChanges', () => {
  it('returns empty no-change state for null response', () => {
    const summary = buildChangeSummary(null)

    expect(summary.riskLevel).toBe('none')
    expect(summary.severityLabel).toBe('无风险')
    expect(summary.nodeChanges).toEqual([])
    expect(summary.edgeChanges).toEqual([])
    expect(summary.intakeChanges).toEqual([])
    expect(summary.riskFlags).toEqual([])
    expect(summary.changedNodeKeys).toEqual(new Set())
  })

  it('returns empty state for invalid response', () => {
    const response = makeResponse({ valid: false, summary: null })

    const summary = buildChangeSummary(response)

    expect(summary.riskLevel).toBe('none')
    expect(summary.nodeChanges).toEqual([])
  })

  it('collects changed node keys from node and edge changes', () => {
    const response = makeResponse({
      summary: {
        risk_level: 'breaking',
        node_changes: [
          makeNodeChange({ node_key: 'node_a', risk: 'breaking' }),
        ],
        edge_changes: [
          makeEdgeChange({
            source: 'node_b',
            target: 'node_c',
            risk: 'breaking',
          }),
        ],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [],
      },
    })

    const summary = buildChangeSummary(response)

    expect(summary.changedNodeKeys).toEqual(
      new Set(['node_a', 'node_b', 'node_c'])
    )
  })

  it('carries node_type into normalized node changes', () => {
    const response = makeResponse({
      summary: {
        risk_level: 'info',
        node_changes: [
          makeNodeChange({ node_key: '_start', node_type: 'start' }),
          makeNodeChange({ node_key: 'node_a' }),
        ],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [],
      },
    })

    const summary = buildChangeSummary(response)

    expect(summary.nodeChanges.map((change) => change.nodeType)).toEqual([
      'start',
      'node',
    ])
  })

  it('sorts risk flags with breaking first', () => {
    const response = makeResponse({
      summary: {
        risk_level: 'breaking',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [
          makeRiskFlag({ code: 'info_1', severity: 'info' }),
          makeRiskFlag({ code: 'warning_1', severity: 'warning' }),
          makeRiskFlag({ code: 'breaking_1', severity: 'breaking' }),
          makeRiskFlag({ code: 'info_2', severity: 'info' }),
        ],
      },
    })

    const summary = buildChangeSummary(response)

    expect(summary.riskFlags.map((flag) => flag.severity)).toEqual([
      'breaking',
      'warning',
      'info',
      'info',
    ])
  })

  it('severity sorting is stable for equal severities', () => {
    const response = makeResponse({
      summary: {
        risk_level: 'info',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [
          makeRiskFlag({ code: 'first', severity: 'info' }),
          makeRiskFlag({ code: 'second', severity: 'info' }),
          makeRiskFlag({ code: 'third', severity: 'info' }),
        ],
      },
    })

    const summary = buildChangeSummary(response)

    expect(summary.riskFlags.map((flag) => flag.code)).toEqual([
      'first',
      'second',
      'third',
    ])
  })

  it('orders severities correctly', () => {
    expect(severityOrder('breaking', 'warning')).toBeLessThan(0)
    expect(severityOrder('warning', 'info')).toBeLessThan(0)
    expect(severityOrder('info', 'none')).toBeLessThan(0)
    expect(severityOrder('warning', 'breaking')).toBeGreaterThan(0)
    expect(severityOrder('info', 'info')).toBe(0)
  })

  it('maps error categories to Chinese labels', () => {
    expect(categoryLabelForError('yaml')).toBe('YAML解析')
    expect(categoryLabelForError('schema')).toBe('结构校验')
    expect(categoryLabelForError('structure')).toBe('结构')
    expect(categoryLabelForError('executor')).toBe('执行器')
    expect(categoryLabelForError('revision')).toBe('版本')
    expect(categoryLabelForError('unknown')).toBe('unknown')
  })

  it('formats node change text', () => {
    const added: NodeChange = {
      type: 'added',
      node_key: 'new_node',
      label: '新节点',
      node_type: 'node',
      fields: [],
      risk: 'info',
    }
    const modified: NodeChange = {
      type: 'modified',
      node_key: 'changed_node',
      label: '变更节点',
      node_type: 'node',
      fields: ['capability', 'outputs'],
      risk: 'breaking',
    }

    expect(
      formatNodeChange(
        buildChangeSummary(
          makeResponse({
            summary: {
              risk_level: 'info',
              node_changes: [added],
              edge_changes: [],
              intake_changes: [],
              metadata_changes: [],
              risk_flags: [],
            },
          })
        ).nodeChanges[0]
      )
    ).toBe('新增节点 新节点')

    expect(
      formatNodeChange(
        buildChangeSummary(
          makeResponse({
            summary: {
              risk_level: 'breaking',
              node_changes: [modified],
              edge_changes: [],
              intake_changes: [],
              metadata_changes: [],
              risk_flags: [],
            },
          })
        ).nodeChanges[0]
      )
    ).toBe('变更节点: 能力、输出')
  })

  it('formats edge condition change text', () => {
    const change = buildChangeSummary(
      makeResponse({
        summary: {
          risk_level: 'breaking',
          node_changes: [],
          edge_changes: [makeEdgeChange()],
          intake_changes: [],
          metadata_changes: [],
          risk_flags: [],
        },
      })
    ).edgeChanges[0]

    expect(formatEdgeChange(change)).toBe(
      'source_a → target_a: $.eligible == true → $.eligible === true'
    )
  })

  it('normalizes metadata changes', () => {
    const response = makeResponse({
      summary: {
        risk_level: 'info',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [
          {
            type: 'modified',
            field: 'label',
            before_value: 'Old',
            after_value: 'New',
            risk: 'info',
          },
        ],
        risk_flags: [],
      },
    })

    const summary = buildChangeSummary(response)

    expect(summary.metadataChanges).toHaveLength(1)
    expect(summary.metadataChanges[0]).toEqual({
      type: 'modified',
      field: 'label',
      beforeValue: 'Old',
      afterValue: 'New',
      severity: 'info',
    })
  })
})
