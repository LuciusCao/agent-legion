import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useUiStore } from '../../stores/uiStore'
import {
  cancelCampaign,
  pauseCampaign,
  resumeCampaign,
} from '../../api/campaignApi'
import { useCampaign } from '../../hooks/useCampaign'
import { BatchList } from './BatchList'
import { makeCampaign } from './testHelpers'

// 「批量任务」列表的组件测试（mock transport，对照 BatchUpgradeDialog /
// WorkspaceMainPage.batch 的形态）：人话名/副行、类型 Chip、状态徽章、
// 进度条、创建/完成时间、展开详情、暂停⇄恢复/取消操作与新建向导入口。

vi.mock('../../api/campaignApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/campaignApi')>()
  return {
    ...actual,
    pauseCampaign: vi.fn(),
    resumeCampaign: vi.fn(),
    cancelCampaign: vi.fn(),
  }
})

vi.mock('../../hooks/useCampaign', () => ({
  useCampaign: vi.fn(),
}))

const mockPauseCampaign = vi.mocked(pauseCampaign)
const mockResumeCampaign = vi.mocked(resumeCampaign)
const mockCancelCampaign = vi.mocked(cancelCampaign)
const mockUseCampaign = vi.mocked(useCampaign)

function renderList(campaigns = [makeCampaign()]) {
  return render(
    <TestQueryProvider>
      <BatchList
        workspaceId="ws1"
        campaigns={campaigns}
        loading={false}
        error={null}
      />
    </TestQueryProvider>
  )
}

describe('BatchList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPauseCampaign.mockResolvedValue({ campaign: makeCampaign() })
    mockResumeCampaign.mockResolvedValue({ campaign: makeCampaign() })
    mockCancelCampaign.mockResolvedValue({ campaign: makeCampaign() })
    mockUseCampaign.mockReturnValue({
      data: undefined,
      error: null,
    } as ReturnType<typeof useCampaign>)
    useUiStore.setState({ toast: null })
  })

  it('renders rows with display name, sub label, mode chip, and status badge', () => {
    renderList([
      makeCampaign({ id: 'camp-newest', status: 'completed' }),
      makeCampaign({
        id: 'camp-older',
        mode: 'submit',
        status: 'running',
        name: '',
        target_spec: { items: [{}, {}], name: undefined },
      }),
    ])
    // 人话名：name 有值直接显示；无值按类型+时间派生。
    expect(screen.getByText('重跑 · 全部失败任务')).toBeInTheDocument()
    expect(
      screen.getByText(/添加 · \d{2}-\d{2} \d{2}:\d{2}/)
    ).toBeInTheDocument()
    // 副行说明 + 类型 Chip + 状态徽章。
    expect(screen.getAllByText('按当前筛选条件全量执行').length).toBe(1)
    expect(screen.getByText('清单 2 条 · 粘贴')).toBeInTheDocument()
    expect(screen.getByTestId('batch-mode-rerun')).toBeInTheDocument()
    expect(screen.getByTestId('batch-mode-submit')).toBeInTheDocument()
    expect(screen.getByTestId('batch-status-completed')).toBeInTheDocument()
    expect(screen.getByTestId('batch-status-running')).toBeInTheDocument()
    // 实现词不进文案。
    expect(screen.queryByText(/campaign/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Campaign/)).not.toBeInTheDocument()
  })

  it('shows progress text with percentage and a determinate bar', () => {
    renderList([
      makeCampaign({
        mode: 'submit',
        target_spec: { items: Array.from({ length: 10 }, () => ({})) },
        progress: { item_offset: 5 },
      }),
    ])
    expect(screen.getByText('5 / 10（50%）')).toBeInTheDocument()
    expect(screen.getByTestId('batch-progress-bar')).toHaveAttribute(
      'aria-valuenow',
      '50'
    )
  })

  it('shows created and finished times', () => {
    renderList([
      makeCampaign({
        status: 'completed',
        created_at: '2026-09-08T01:30:00Z',
        finished_at: '2026-09-08T02:47:00Z',
      }),
    ])
    expect(screen.getByTestId('batch-created-at').textContent).toContain('2026')
    expect(screen.getByTestId('batch-finished-at').textContent).toContain(
      '2026'
    )
  })

  it('shows an empty-state hint when no batches exist', () => {
    renderList([])
    expect(screen.getByText(/尚无批量任务/)).toBeInTheDocument()
  })

  it('shows the loading spinner on first load', () => {
    render(
      <TestQueryProvider>
        <BatchList workspaceId="ws1" campaigns={[]} loading error={null} />
      </TestQueryProvider>
    )
    expect(document.querySelector('[role="progressbar"]')).not.toBeNull()
  })

  it('shows the error alert on load failure', () => {
    render(
      <TestQueryProvider>
        <BatchList
          workspaceId="ws1"
          campaigns={[]}
          loading={false}
          error="boom"
        />
      </TestQueryProvider>
    )
    expect(screen.getByText(/批量任务列表加载失败：boom/)).toBeInTheDocument()
  })

  it('expands the detail panel on row click', async () => {
    mockUseCampaign.mockReturnValue({
      data: { campaign: makeCampaign() },
      error: null,
    } as ReturnType<typeof useCampaign>)
    renderList()
    await act(async () => {
      fireEvent.click(screen.getByText('重跑 · 全部失败任务'))
    })
    await waitFor(() => {
      expect(screen.getByTestId('batch-detail')).toBeInTheDocument()
    })
  })

  it('offers pause and cancel for a running batch', async () => {
    renderList()
    await act(async () => {
      fireEvent.click(screen.getByText('取消'))
    })
    await waitFor(() => {
      expect(mockCancelCampaign).toHaveBeenCalledWith('ws1', 'camp-0001')
    })
    expect(screen.queryByText('恢复')).not.toBeInTheDocument()
  })

  it('hides pause and shows resume for a paused batch', async () => {
    renderList([makeCampaign({ status: 'paused' })])
    expect(screen.queryByText('暂停')).not.toBeInTheDocument()
    await act(async () => {
      fireEvent.click(screen.getByText('恢复'))
    })
    await waitFor(() => {
      expect(mockResumeCampaign).toHaveBeenCalledWith('ws1', 'camp-0001')
    })
  })

  it('hides lifecycle actions for terminal batches', () => {
    renderList([makeCampaign({ status: 'cancelled' })])
    expect(screen.queryByText('暂停')).not.toBeInTheDocument()
    expect(screen.queryByText('恢复')).not.toBeInTheDocument()
    expect(screen.queryByText('取消')).not.toBeInTheDocument()
  })

  it('opens the create wizard from the toolbar button', async () => {
    renderList([])
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-create-button'))
    })
    // 工具栏按钮与向导 DialogTitle 同名，取标题断言向导已打开。
    expect(screen.getAllByText('新建批量任务').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('batch-wizard-stepper')).toBeInTheDocument()
  })
})
