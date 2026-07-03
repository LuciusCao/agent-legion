import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowPublishReviewDialog } from './WorkflowPublishReviewDialog'
import { buildChangeSummary } from '../workflowStudioChanges'
import type { components } from '../../../generated/api'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']
type WorkflowRevisionSummary = components['schemas']['WorkflowRevisionSummary']

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

const revision: WorkflowRevisionSummary = {
  id: 'ws1:demo:v1',
  workspace_id: 'ws1',
  workflow_key: 'demo',
  version: 1,
  status: 'active',
  definition_hash: 'abcdef1234567890',
  created_at: '2026-07-02T00:00:00Z',
  published_at: '2026-07-02T00:00:00Z',
}

describe('WorkflowPublishReviewDialog', () => {
  it('renders version and workflow metadata', () => {
    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={buildChangeSummary(makeSummaryResponse())}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('发布 workflow revision')).toBeInTheDocument()
    expect(screen.getByText(/当前 active v1/)).toBeInTheDocument()
    expect(screen.getByText(/新 revision v2/)).toBeInTheDocument()
    expect(screen.getByText('demo')).toBeInTheDocument()
    expect(screen.getByText('abcdef12')).toBeInTheDocument()
  })

  it('does not call confirm without changes', async () => {
    const onConfirm = vi.fn()
    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={buildChangeSummary(makeSummaryResponse())}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByRole('button', { name: '确认发布' })).toBeDisabled()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('calls confirm once when user explicitly confirms', async () => {
    const onConfirm = vi.fn()
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'info',
          node_changes: [
            {
              type: 'added',
              node_key: 'new_node',
              label: '新节点',
              fields: [],
              risk: 'info',
            },
          ],
          edge_changes: [],
          intake_changes: [],
          metadata_changes: [],
          risk_flags: [],
        },
      })
    )

    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: '确认发布' }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls cancel and does not call confirm when returning to edit', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'breaking',
          node_changes: [
            {
              type: 'removed',
              node_key: 'deleted',
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
    )

    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: '返回编辑' }))

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('keeps breaking risk publishable after explicit confirmation', async () => {
    const onConfirm = vi.fn()
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'breaking',
          node_changes: [
            {
              type: 'removed',
              node_key: 'deleted',
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
    )

    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('删除节点会导致下游路径断开。')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '确认发布' }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('shows metadata changes in the review dialog', () => {
    const summary = buildChangeSummary(
      makeSummaryResponse({
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
    )

    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('元数据变更: 1')).toBeInTheDocument()
    expect(screen.getByText('Workflow 名称: Old → New')).toBeInTheDocument()
  })

  it('shows breaking chip for schema_version metadata change', () => {
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'breaking',
          node_changes: [],
          edge_changes: [],
          intake_changes: [],
          metadata_changes: [
            {
              type: 'modified',
              field: 'schema_version',
              before_value: '2',
              after_value: '3',
              risk: 'breaking',
            },
          ],
          risk_flags: [],
        },
      })
    )

    render(
      <WorkflowPublishReviewDialog
        open
        workflowKey="demo"
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getAllByText('高风险').length).toBeGreaterThanOrEqual(1)
  })
})
