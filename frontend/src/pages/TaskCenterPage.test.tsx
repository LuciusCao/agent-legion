import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import TaskCenterPage from './TaskCenterPage'
import {
  archiveRunBatch,
  cancelRunBatch,
  listRunBatches,
  pauseRunBatch,
  resumeRunBatch,
} from '../api/runBatchesApi'
import { useUiStore } from '../stores/uiStore'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api/runBatchesApi', () => ({
  listRunBatches: vi.fn(),
  archiveRunBatch: vi.fn(),
  pauseRunBatch: vi.fn(),
  resumeRunBatch: vi.fn(),
  cancelRunBatch: vi.fn(),
}))

const mockList = vi.mocked(listRunBatches)
const mockArchive = vi.mocked(archiveRunBatch)
const mockPause = vi.mocked(pauseRunBatch)
const mockResume = vi.mocked(resumeRunBatch)
const mockCancel = vi.mocked(cancelRunBatch)

function batch(overrides: Record<string, unknown>) {
  return {
    run_id: 'abcdef1234567890',
    status: 'running',
    items_total: 2400,
    processed_count: 1200,
    jobs_created: 1000,
    chunk_errors: [],
    error_message: '',
    created_at: '2026-10-21T02:21:00+00:00',
    updated_at: '2026-10-21T02:24:00+00:00',
    finished_at: null,
    created_by: 'tester',
    archived_at: null,
    archive_source: null,
    failed_item_count: 0,
    failed_item_count_lower_bound: false,
    ...overrides,
  }
}

function page(batches: unknown[], activeCount = 0) {
  return {
    batches,
    next_cursor: null,
    active_count: activeCount,
  }
}

function renderPage(entry = '/workspaces/ws1/tasks') {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/tasks"
            element={<TaskCenterPage />}
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('TaskCenterPage（#1157 phase3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUiStore.setState({ toast: null })
    mockList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'active')
        return Promise.resolve(page([], 0) as never)
      return Promise.resolve(page([], 0) as never)
    })
  })

  it('renders active and ended sections with self-contained cards', async () => {
    mockList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'active')
        return Promise.resolve(
          page(
            [
              batch({ run_id: 'run-running-1', status: 'running' }),
              batch({
                run_id: 'run-queued-1',
                status: 'pending',
                processed_count: 0,
                jobs_created: 0,
              }),
            ],
            2
          ) as never
        )
      if (options.view === 'ended')
        return Promise.resolve(
          page(
            [
              batch({
                run_id: 'run-done-1',
                status: 'completed',
                processed_count: 2400,
                jobs_created: 2130,
                finished_at: '2026-10-21T01:53:00+00:00',
              }),
              batch({
                run_id: 'run-partial-1',
                status: 'completed',
                items_total: 3000,
                processed_count: 3000,
                jobs_created: 2588,
                chunk_errors: [
                  { chunk_start: 2800, error: '外部连接 cms-main 响应超时' },
                ],
                failed_item_count: 200,
                finished_at: '2026-10-21T01:36:00+00:00',
              }),
            ],
            2
          ) as never
        )
      return Promise.resolve(page([], 2) as never)
    })
    renderPage()

    // 汇总条：进行中 / 排队中计数。
    expect(await screen.findByText('任务中心')).toBeInTheDocument()
    // 等 active 查询落地（异步）：进行中卡片先行断言。
    const runningCard = await screen.findByTestId('task-card-run-running-1')
    expect(screen.getByText('进行中与排队')).toBeInTheDocument()
    expect(screen.getByText('已结束')).toBeInTheDocument()

    // 进行中卡片：状态 badge + 进度计数 + 新建/跳过明细 + 暂停/取消。
    expect(runningCard).toHaveTextContent('进行中')
    expect(runningCard).toHaveTextContent('已处理 1,200 / 2,400')
    expect(runningCard).toHaveTextContent('新建 1,000')
    expect(runningCard).toHaveTextContent('跳过 200（已有任务）')

    // 终态卡片：全部完成 + 查看任务 + 归档。
    const doneCard = screen.getByTestId('task-card-run-done-1')
    expect(doneCard).toHaveTextContent('已完成')
    expect(doneCard).toHaveTextContent('2,400 / 2,400 全部完成')
    expect(doneCard).toHaveTextContent('跳过 270')

    // 部分完成卡片（reviewer r1 P2-1）：跳过与失败拆开，失败不贴「已有任务」。
    const partialCard = screen.getByTestId('task-card-run-partial-1')
    expect(partialCard).toHaveTextContent('部分完成')
    expect(partialCard).toHaveTextContent('新建 2,588')
    expect(partialCard).toHaveTextContent('跳过 212（已有任务）')
    expect(partialCard).toHaveTextContent('失败 200')
    expect(partialCard).toHaveTextContent('外部连接 cms-main 响应超时')
  })

  it('runs the control plane from running cards（暂停/继续/取消）', async () => {
    mockList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'active')
        return Promise.resolve(
          page(
            [
              batch({ run_id: 'run-running-1', status: 'running' }),
              batch({ run_id: 'run-paused-1', status: 'paused' }),
            ],
            2
          ) as never
        )
      return Promise.resolve(page([], 2) as never)
    })
    mockPause.mockResolvedValue({} as never)
    mockResume.mockResolvedValue({} as never)
    mockCancel.mockResolvedValue({} as never)
    renderPage()

    const runningCard = await screen.findByTestId('task-card-run-running-1')
    fireEvent.click(
      Array.from(runningCard.querySelectorAll('button')).find(
        (b) => b.textContent === '暂停'
      )!
    )
    // mutation 在微任务里触发（等信号非等时长）。
    await waitFor(() =>
      expect(mockPause).toHaveBeenCalledWith('ws1', 'run-running-1')
    )

    const pausedCard = await screen.findByTestId('task-card-run-paused-1')
    expect(pausedCard).toHaveTextContent(
      '已暂停 · 后台处理已挂起，已处理部分保留'
    )
    fireEvent.click(
      Array.from(pausedCard.querySelectorAll('button')).find(
        (b) => b.textContent === '继续'
      )!
    )
    await waitFor(() =>
      expect(mockResume).toHaveBeenCalledWith('ws1', 'run-paused-1')
    )

    fireEvent.click(
      Array.from(pausedCard.querySelectorAll('button')).find(
        (b) => b.textContent === '取消'
      )!
    )
    await waitFor(() =>
      expect(mockCancel).toHaveBeenCalledWith('ws1', 'run-paused-1')
    )
  })

  it('archives a terminal card and toasts the prototype wording', async () => {
    mockList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'ended')
        return Promise.resolve(
          page(
            [
              batch({
                run_id: 'run-done-1',
                status: 'completed',
                processed_count: 2400,
                jobs_created: 2400,
                finished_at: '2026-10-21T01:53:00+00:00',
              }),
            ],
            0
          ) as never
        )
      return Promise.resolve(page([], 0) as never)
    })
    mockArchive.mockResolvedValue({} as never)
    renderPage()

    const doneCard = await screen.findByTestId('task-card-run-done-1')
    fireEvent.click(
      Array.from(doneCard.querySelectorAll('button')).find(
        (b) => b.textContent === '归档'
      )!
    )
    await waitFor(() =>
      expect(mockArchive).toHaveBeenCalledWith('ws1', 'run-done-1')
    )
    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message: '已归档。记录移到任务中心右上角「已归档」，随时可以查看。',
        type: 'success',
      })
    )
  })

  it('shows the empty-state guidance when there are no batches', async () => {
    renderPage()
    expect(await screen.findByText(/当前没有批量操作记录/)).toBeInTheDocument()
    expect(screen.getByText(/结束超过 7 天的记录自动移入/)).toBeInTheDocument()
  })

  it('renders the archived view read-only with provenance lines', async () => {
    mockList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'archived')
        return Promise.resolve(
          page(
            [
              batch({
                run_id: 'run-manual-1',
                status: 'completed',
                processed_count: 2400,
                jobs_created: 2400,
                finished_at: '2026-10-10T01:53:00+00:00',
                archived_at: '2026-10-12T01:00:00+00:00',
                archive_source: 'manual',
              }),
              batch({
                run_id: 'run-auto-1',
                status: 'completed',
                processed_count: 100,
                jobs_created: 100,
                finished_at: '2026-10-01T01:53:00+00:00',
                archive_source: 'auto',
              }),
            ],
            0
          ) as never
        )
      return Promise.resolve(page([], 0) as never)
    })
    renderPage('/workspaces/ws1/tasks?view=archived')

    expect(await screen.findByText('已归档')).toBeInTheDocument()
    const manualCard = await screen.findByTestId('task-card-run-manual-1')
    expect(manualCard).toHaveTextContent('手动归档')
    const autoCard = await screen.findByTestId('task-card-run-auto-1')
    expect(autoCard).toHaveTextContent('自动归档（结束超过 7 天）')
    // 只读：归档视图不渲染任何操作按钮。
    expect(manualCard.querySelectorAll('button')).toHaveLength(0)
    expect(autoCard.querySelectorAll('button')).toHaveLength(0)
  })
})
