import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useCampaign } from '../../hooks/useCampaign'
import { BatchDetail } from './BatchDetail'
import { makeCampaign } from './testHelpers'

// 「批量任务」详情面板的组件测试（mock useCampaign transport）：
// 执行节奏 / 目标进度 / 水位 sparkline / 失败原因与修复指引 /
// 添加类任务的关联运行 / 连续失败告警 / ID 只进详情。

vi.mock('../../hooks/useCampaign', () => ({
  useCampaign: vi.fn(),
}))

const mockUseCampaign = vi.mocked(useCampaign)

function renderDetail(campaign = makeCampaign()) {
  mockUseCampaign.mockReturnValue({
    data: { campaign },
    error: null,
  } as ReturnType<typeof useCampaign>)
  return render(
    <TestQueryProvider>
      <BatchDetail workspaceId="ws1" campaignId={campaign.id} />
    </TestQueryProvider>
  )
}

describe('BatchDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows status badge, mode chip, count summary, and the batch id', () => {
    renderDetail()
    expect(screen.getByTestId('batch-status-running')).toBeInTheDocument()
    expect(screen.getByTestId('batch-mode-rerun')).toBeInTheDocument()
    expect(
      screen.getByText(/目标进度 · 成功 100 · 跳过 15 · 失败 5/)
    ).toBeInTheDocument()
    expect(screen.getByText(/ID camp-0001/)).toBeInTheDocument()
  })

  it('shows the pacing line (watermark and batch size)', () => {
    renderDetail()
    expect(screen.getByText(/队列水位线 30,000/)).toBeInTheDocument()
    expect(screen.getByText(/每批 5,000 条/)).toBeInTheDocument()
    expect(screen.getByText(/已投放 2 批/)).toBeInTheDocument()
  })

  it('shows the processed cursor note for filter-mode batches', () => {
    renderDetail()
    expect(screen.getByText(/已处理 120 个目标/)).toBeInTheDocument()
  })

  it('renders a percentage progress bar for snapshot cursors', () => {
    renderDetail(
      makeCampaign({
        mode: 'submit',
        target_spec: { manifest_item_count: 200 },
        progress: { item_offset: 100 },
      })
    )
    expect(screen.getByText(/100 \/ 200（50%）/)).toBeInTheDocument()
    expect(screen.getByTestId('batch-cursor-progress')).toHaveAttribute(
      'aria-valuenow',
      '50'
    )
  })

  it('renders the watermark sparkline when samples exist', () => {
    renderDetail(
      makeCampaign({
        progress: {
          cursor: null,
          processed: 2,
          watermark_samples: [
            { level: 5000, ts: 1 },
            { level: 8000, ts: 2 },
            { level: 12000, ts: 3 },
          ],
        },
      })
    )
    expect(screen.getByTestId('batch-watermark-sparkline')).toBeInTheDocument()
    expect(screen.getByText(/水位轨迹（最近 3 次采样/)).toBeInTheDocument()
  })

  it('hides the sparkline with fewer than two samples', () => {
    renderDetail(
      makeCampaign({ progress: { cursor: null, watermark_samples: [] } })
    )
    expect(
      screen.queryByTestId('batch-watermark-sparkline')
    ).not.toBeInTheDocument()
  })

  it('surfaces error_message with the repair guidance for failed batches', () => {
    renderDetail(
      makeCampaign({ status: 'failed', error_message: '清单文件损坏' })
    )
    expect(screen.getByText(/失败原因：清单文件损坏/)).toBeInTheDocument()
    expect(
      screen.getByText(/修复问题后可重新创建同类批量任务/)
    ).toBeInTheDocument()
  })

  it('shows the consecutive-failure warning from progress_json', () => {
    renderDetail(makeCampaign({ progress: { consecutive_failures: 3 } }))
    expect(screen.getByText(/连续投放失败 3 次/)).toBeInTheDocument()
  })

  it('lists the linked runs overview for submit batches', () => {
    renderDetail(
      makeCampaign({
        mode: 'submit',
        // PR-C 的 detail 聚合字段；campaignRuns 按「可能缺席」读取。
        ...{
          runs: [
            {
              id: 'run-aaaa-bbbb',
              status: 'created',
              created_count: 50,
              job_count: 50,
            },
          ],
        },
      })
    )
    expect(screen.getByText(/关联运行（1）/)).toBeInTheDocument()
    expect(screen.getByText(/run-aaaa/)).toBeInTheDocument()
    expect(screen.getByText(/新建 50/)).toBeInTheDocument()
  })
})
