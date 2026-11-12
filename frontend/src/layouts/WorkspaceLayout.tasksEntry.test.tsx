import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceLayout from './WorkspaceLayout'
import { useTaskCenterStore } from '../stores/taskCenterStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn().mockResolvedValue({
    workspaces: [{ id: 'ws1', name: '测试空间' }],
  }),
}))

vi.mock('../views/WorkspaceJobList', () => ({
  default: () => <div data-testid="job-list">JobList</div>,
}))
vi.mock('../views/WorkspaceJobDetail', () => ({
  default: () => <div data-testid="job-detail">JobDetail</div>,
}))

const activeCountState = { count: 0 }
const activeOpCountState = { count: 0 }
vi.mock('../features/taskCenter/useRunBatches', () => ({
  useActiveRunBatches: () => ({
    data:
      activeCountState.count > 0
        ? {
            batches: [],
            next_cursor: null,
            active_count: activeCountState.count,
          }
        : undefined,
  }),
  runBatchKeys: {
    all: (id: string) => ['run-batches', id],
    view: (id: string, view: string) => ['run-batches', id, view],
  },
}))

vi.mock('../features/taskCenter/useJobBatchOpListing', () => ({
  useActiveJobBatchOps: () => ({
    data:
      activeOpCountState.count > 0
        ? {
            operations: [],
            next_cursor: null,
            active_count: activeOpCountState.count,
          }
        : undefined,
  }),
}))

function renderLayout(initialEntry = '/workspaces/ws1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/*"
          element={<WorkspaceLayout />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkspaceLayout 任务中心入口（#1157 phase3）', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    activeCountState.count = 0
    activeOpCountState.count = 0
    useTaskCenterStore.setState({ acceptedNotice: null })
  })

  it('navigates to the task center from the app bar entry', () => {
    renderLayout()
    fireEvent.click(screen.getByLabelText('任务中心'))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/tasks')
  })

  it('shows the badge only when batches are active（计数=进行中+排队）', () => {
    const { rerender } = renderLayout()
    expect(screen.queryByTestId('tasks-badge')).not.toBeInTheDocument()

    activeCountState.count = 3
    rerender(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('tasks-badge')).toHaveTextContent('3')

    // #946 M52：批量操作 active 计数并入徽标。
    activeOpCountState.count = 2
    rerender(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('tasks-badge')).toHaveTextContent('5')
  })

  it('renders the accept toast with 查看进度 after an async accept', () => {
    renderLayout()
    act(() => {
      useTaskCenterStore.getState().showAcceptedNotice({
        workspaceId: 'ws1',
        itemsTotal: 10000,
        runId: 'run-x',
      })
    })
    const toast = screen.getByTestId('task-accept-toast')
    expect(toast).toHaveTextContent('已受理 10,000 个条目，正在后台创建。')
    expect(toast).toHaveTextContent('进度见右上角「任务」')

    fireEvent.click(screen.getByRole('button', { name: '查看进度 →' }))
    expect(mockNavigate).toHaveBeenCalledWith('/workspaces/ws1/tasks')
    expect(useTaskCenterStore.getState().acceptedNotice).toBeNull()
  })

  it('ignores accept notices bound to another workspace', () => {
    renderLayout()
    act(() => {
      useTaskCenterStore.getState().showAcceptedNotice({
        workspaceId: 'ws-other',
        itemsTotal: 2000,
        runId: 'run-y',
      })
    })
    expect(screen.queryByTestId('task-accept-toast')).not.toBeInTheDocument()
  })

  it('renders the batch-op accept toast with the kind wording（#946 M52）', () => {
    renderLayout()
    act(() => {
      useTaskCenterStore.getState().showAcceptedNotice({
        workspaceId: 'ws1',
        itemsTotal: 20000,
        opId: 'op-x',
        opKind: 'rerun',
      })
    })
    const toast = screen.getByTestId('task-accept-toast')
    expect(toast).toHaveTextContent('已受理 20,000 个任务，正在后台重跑。')
  })
})
