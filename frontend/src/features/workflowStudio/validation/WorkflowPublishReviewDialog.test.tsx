import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowPublishReviewDialog } from './WorkflowPublishReviewDialog'
import { buildChangeSummary } from './workflowStudioChanges'
import type { components } from '../../../generated/api'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']
type WorkflowRevisionSummary = components['schemas']['WorkflowRevisionSummary']

function makeSummaryResponse(
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

const revision: WorkflowRevisionSummary = {
  id: 'ws1:demo:v1',
  workspace_id: 'ws1',
  version: 1,
  status: 'active',
  definition_hash: 'abcdef1234567890',
  created_at: '2026-07-02T00:00:00Z',
  published_at: '2026-07-02T00:00:00Z',
}

describe('WorkflowPublishReviewDialog', () => {
  it('审 A 发 B：contentStale 时确认键禁用并提示重审（#804 轮 4 P2-C）', () => {
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'info',
          node_changes: [
            {
              type: 'modified',
              node_key: 'a',
              label: 'A',
              node_type: 'code',
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
    const onConfirm = vi.fn()
    render(
      <WorkflowPublishReviewDialog
        open
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
        contentStale
      />
    )

    expect(screen.getByText(/草稿内容在你审阅期间已被更新/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认发布' })).toBeDisabled()
  })

  it('renders version and workflow metadata', () => {
    render(
      <WorkflowPublishReviewDialog
        open
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
    expect(screen.getByText('abcdef12')).toBeInTheDocument()
    // #1127：「Workflow Key」概念已退役（DB-WORKSPACE-KEY-BINDING-001，
    // workspace id 即 workflow 身份），meta 网格不再展示该行。
    expect(screen.queryByText('Workflow Key')).not.toBeInTheDocument()
  })

  it('explains that runtime-only changes keep the current revision', () => {
    render(
      <WorkflowPublishReviewDialog
        open
        activeRevision={revision}
        nextVersion={2}
        createsRevision={false}
        definitionHash="abcdef1234567890"
        summary={buildChangeSummary(
          makeSummaryResponse({
            creates_revision: false,
            summary: {
              ...makeSummaryResponse().summary!,
              node_changes: [
                {
                  type: 'modified',
                  node_key: 'generate',
                  label: '生成',
                  node_type: 'code',
                  fields: ['execution'],
                  risk: 'warning',
                },
              ],
            },
          })
        )}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('保存节点运行配置')).toBeInTheDocument()
    expect(screen.getByText(/不创建新 revision/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认保存' })).toBeEnabled()
  })

  it('does not call confirm without changes', async () => {
    const onConfirm = vi.fn()
    render(
      <WorkflowPublishReviewDialog
        open
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

  it('M49 runtime-only 确认（compare 无条目 + 有 runtime 键）：确认可用，文案按不创建新 revision 口径', () => {
    // agent 发布请求的 tools/requires_labels-only 变更：M47 起它们不进
    // compare，summary 恒空——旧门禁（只看 compare 摘要）把确认永久禁用，
    // 是双通道死端。现在 runtime 键存在即可确认（确认端点按 runtime-only
    // 原地生效 + 追加配置版本），文案不再说「写入新版本」。
    render(
      <WorkflowPublishReviewDialog
        open
        activeRevision={revision}
        nextVersion={2}
        createsRevision={false}
        definitionHash="abcdef1234567890"
        summary={buildChangeSummary(makeSummaryResponse())}
        runtimeChangedKeys={['nodes.generate.tools']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('保存节点运行配置')).toBeInTheDocument()
    expect(
      screen.getByText(/无结构变更，本次只保存运行配置/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/确认后立即对新执行生效并追加一条配置版本/)
    ).toBeInTheDocument()
    expect(screen.queryByText('没有可发布的变更')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认保存' })).toBeEnabled()
  })

  it('M49 结构发布 + 未保存 runtime 键：保持「随发布写入新版本」口径且确认可用', () => {
    // 混合场景不回归：有结构 diff 时确认门照旧由 compare 摘要满足，
    // runtime 信息条保持 M48 的「随本次发布一并写入新版本」文案。
    const summary = buildChangeSummary(
      makeSummaryResponse({
        creates_revision: true,
        summary: {
          risk_level: 'info',
          node_changes: [
            {
              type: 'modified',
              node_key: 'generate',
              label: '生成',
              node_type: 'code',
              fields: ['label'],
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
        activeRevision={revision}
        nextVersion={2}
        createsRevision
        definitionHash="abcdef1234567890"
        summary={summary}
        runtimeChangedKeys={['nodes.generate.model']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(
      screen.getByText(/尚未通过运行通道保存——将随本次发布一并写入新版本/)
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认发布' })).toBeEnabled()
  })

  it('#1369/#1381: uses the filtered structural risk for mixed execution and label changes', () => {
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'warning',
          node_changes: [
            {
              type: 'modified',
              node_key: 'write_script',
              label: '撰写脚本',
              node_type: 'agent',
              fields: ['label', 'execution'],
              risk: 'warning',
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
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )

    expect(screen.getByText('撰写脚本: 标签')).toBeInTheDocument()
    expect(screen.queryByText(/执行配置/)).not.toBeInTheDocument()
    expect(screen.getAllByText('提示').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('警告')).not.toBeInTheDocument()
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
              node_type: 'code',
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
              node_type: 'code',
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

  it('does not trigger cancel while confirming (return-to-edit is disabled)', async () => {
    // #429 二轮复审 P3：confirm 的 publish 进行中，用户点「返回编辑」（或
    // ESC/backdrop——同一 onClose 通道）不得触发 cancel：revision 实际上线
    // 却落 rejected 回执是误导。按钮 disabled，onClose 回调也早退（双保险，
    // 键盘/点击穿透同一守卫）。
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'info',
          node_changes: [
            {
              type: 'added',
              node_key: 'new_node',
              label: '新节点',
              node_type: 'code',
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
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        confirming
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    )

    expect(screen.getByRole('button', { name: '返回编辑' })).toBeDisabled()
    // ESC 走 Dialog 的 onClose，confirming 期间同样不得触发 cancel。
    await userEvent.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('does not trigger cancel while canceling (ESC and return-to-edit are disabled)', async () => {
    // #429 三轮复审 P3：cancel 进行中，用户再点「返回编辑」或按 ESC（同一
    // onClose 通道）不得二次触发 cancel——第二次 cancel 必 404，红色假失败
    // toast 会与正确回执同现。按钮 disabled，onClose 回调也早退（双保险）。
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const summary = buildChangeSummary(
      makeSummaryResponse({
        summary: {
          risk_level: 'info',
          node_changes: [
            {
              type: 'added',
              node_key: 'new_node',
              label: '新节点',
              node_type: 'code',
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
        activeRevision={revision}
        nextVersion={2}
        definitionHash="abcdef1234567890"
        summary={summary}
        canceling
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    )

    expect(screen.getByRole('button', { name: '返回编辑' })).toBeDisabled()
    // ESC 走 Dialog 的 onClose，canceling 期间同样不得触发 cancel。
    await userEvent.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
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
              node_type: 'code',
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
