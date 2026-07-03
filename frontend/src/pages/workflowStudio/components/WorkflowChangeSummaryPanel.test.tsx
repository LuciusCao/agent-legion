import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowChangeSummaryPanel } from './WorkflowChangeSummaryPanel'
import { buildChangeSummary } from '../workflowStudioChanges'
import styles from './WorkflowChangeSummaryPanel.module.css'
import type { components } from '../../../generated/api'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']
type CompareError = components['schemas']['WorkflowDraftCompareError']

function makeSummaryResponse(
  overrides: Partial<CompareResponse> = {}
): CompareResponse {
  return {
    valid: true,
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

describe('WorkflowChangeSummaryPanel', () => {
  it('renders synced state when there are no changes', () => {
    const summary = buildChangeSummary(makeSummaryResponse())

    render(
      <WorkflowChangeSummaryPanel
        summary={summary}
        loading={false}
        errors={null}
      />
    )

    expect(screen.getByText('变更摘要')).toBeInTheDocument()
    expect(
      screen.getByText('已同步 — 当前 YAML 与 active revision 一致')
    ).toBeInTheDocument()
  })

  it('renders loading state', () => {
    render(
      <WorkflowChangeSummaryPanel summary={null} loading={true} errors={null} />
    )

    expect(screen.getByText('正在对比...')).toBeInTheDocument()
  })

  it('renders invalid YAML errors grouped by category', () => {
    const errors: CompareError[] = [
      {
        category: 'yaml',
        message: "could not find expected ':'",
        line: 18,
        column: 7,
      },
      {
        category: 'schema',
        message: 'Workflow nodes are required',
      },
    ]

    render(
      <WorkflowChangeSummaryPanel
        summary={null}
        loading={false}
        errors={errors}
        onSelectNode={vi.fn()}
      />
    )

    expect(screen.getByText('YAML解析')).toBeInTheDocument()
    expect(screen.getByText('结构校验')).toBeInTheDocument()
    expect(screen.getByText("could not find expected ':'")).toBeInTheDocument()
    expect(screen.getByText('位置: 18 行 7 列')).toBeInTheDocument()
  })

  it('calls onSelectNode when error node key is clicked', async () => {
    const onSelectNode = vi.fn()
    const errors: CompareError[] = [
      {
        category: 'schema',
        message: 'Missing capability',
        node_key: 'classify_comprehension_eligibility',
      },
    ]

    render(
      <WorkflowChangeSummaryPanel
        summary={null}
        loading={false}
        errors={errors}
        onSelectNode={onSelectNode}
      />
    )

    await userEvent.click(
      screen.getByText('节点: classify_comprehension_eligibility')
    )

    expect(onSelectNode).toHaveBeenCalledWith(
      'classify_comprehension_eligibility'
    )
  })

  it('renders breaking risk state with severity badge', () => {
    const response = makeSummaryResponse({
      summary: {
        risk_level: 'breaking',
        node_changes: [
          {
            type: 'removed',
            node_key: 'deleted_node',
            label: '被删节点',
            fields: [],
            risk: 'breaking',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [
          {
            code: 'node_removed',
            message: '删除节点会导致下游路径断开。',
            severity: 'breaking',
          },
        ],
      },
    })
    const summary = buildChangeSummary(response)

    render(
      <WorkflowChangeSummaryPanel
        summary={summary}
        loading={false}
        errors={null}
      />
    )

    expect(screen.getByText('风险等级: 高风险')).toBeInTheDocument()
    expect(screen.getByText('删除节点会导致下游路径断开。')).toBeInTheDocument()
    expect(screen.getAllByText('高风险')).toHaveLength(2)
  })

  it('renders metadata changes', () => {
    const response = makeSummaryResponse({
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

    render(
      <WorkflowChangeSummaryPanel
        summary={summary}
        loading={false}
        errors={null}
      />
    )

    expect(screen.getByText('元数据变更')).toBeInTheDocument()
    expect(screen.getByText('Workflow 名称: Old → New')).toBeInTheDocument()
  })

  it('truncates long node keys and edge conditions', () => {
    const longKey = 'very_long_node_key_that_should_be_truncated_in_the_panel'
    const longCondition =
      '$.some_really_long_condition_path.that_should_not_overflow_the_container'
    const response = makeSummaryResponse({
      summary: {
        risk_level: 'warning',
        node_changes: [
          {
            type: 'modified',
            node_key: longKey,
            label: longKey,
            fields: ['label'],
            risk: 'info',
          },
        ],
        edge_changes: [
          {
            type: 'condition_changed',
            source: 'a',
            target: 'b',
            before_condition: longCondition,
            after_condition: longCondition,
            risk: 'breaking',
          },
        ],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [],
      },
    })
    const summary = buildChangeSummary(response)

    const { container } = render(
      <WorkflowChangeSummaryPanel
        summary={summary}
        loading={false}
        errors={null}
        onSelectNode={vi.fn()}
      />
    )

    const texts = container.querySelectorAll('[title]')
    const titles = Array.from(texts).map((el) => el.getAttribute('title'))
    expect(titles.some((title) => title?.includes(longKey))).toBe(true)
    expect(titles.some((title) => title?.includes(longCondition))).toBe(true)

    const items = container.querySelectorAll('[class*="itemText"]')
    expect(items.length).toBeGreaterThan(0)
    items.forEach((item) => {
      expect(item).toHaveClass(styles.itemText)
    })
  })
})
