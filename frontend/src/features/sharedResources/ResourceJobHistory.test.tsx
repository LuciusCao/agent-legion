import { render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { fetchJobDetail, fetchJobsSnapshot } from '../../api'
import { ResourceJobHistory } from './ResourceJobHistory'

vi.mock('../../api', () => ({
  fetchJobsSnapshot: vi.fn(),
  fetchJobDetail: vi.fn(),
}))

function renderHistory() {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={['/workspaces/ws1/resources/resource-1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/resources/:resourceId"
            element={
              <ResourceJobHistory
                workspaceId="ws1"
                resourceId="resource-1"
                resourceName="通用审核规则"
                currentVersion={2}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('ResourceJobHistory', () => {
  beforeEach(() => vi.clearAllMocks())

  it('没有真实任务时显示空历史，不预置示例', async () => {
    vi.mocked(fetchJobsSnapshot).mockResolvedValue({ jobs: [] } as never)
    renderHistory()
    expect(
      await screen.findByText(/最近还没有任务使用过这个资源/)
    ).toBeInTheDocument()
    expect(screen.queryByText(/job-/)).not.toBeInTheDocument()
  })

  it('逐个读取任务实际使用版本，并与当前版本比较', async () => {
    vi.mocked(fetchJobsSnapshot).mockResolvedValue({
      jobs: [
        { id: 'job-new', title: '新任务' },
        { id: 'job-old', title: '旧任务' },
      ],
    } as never)
    vi.mocked(fetchJobDetail).mockImplementation((jobId) =>
      Promise.resolve({
        shared_resources: [
          {
            resource_id: 'resource-1',
            name: '通用审核规则',
            version: jobId === 'job-new' ? 2 : 1,
          },
        ],
      } as never)
    )
    renderHistory()
    expect(await screen.findByText('新任务')).toBeInTheDocument()
    expect(screen.getByText('旧任务')).toBeInTheDocument()
    expect(screen.getByText('与当前使用版本相同')).toBeInTheDocument()
    expect(screen.getByText('当前已改为 v2，本任务不变')).toBeInTheDocument()
    expect(fetchJobDetail).toHaveBeenCalledTimes(2)
  })
})
