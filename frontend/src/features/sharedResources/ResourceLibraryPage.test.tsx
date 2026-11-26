import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeWorkspace } from '../../testing/workspaceFixtures'
import { makeSharedResource } from '../../testing/sharedResourceFixtures'
import {
  listSharedResources,
  shareSharedResource,
} from '../../api/sharedResources'
import { fetchWorkspaces } from '../../api'
import { useAuthStore } from '../../stores/authStore'
import ResourceLibraryPage from './ResourceLibraryPage'

vi.mock('../../api/sharedResources', () => ({
  listSharedResources: vi.fn(),
  listSharedResourceGrants: vi.fn(() => Promise.resolve({ grants: [] })),
  shareSharedResource: vi.fn(),
  revokeSharedResource: vi.fn(),
}))
vi.mock('../../api', () => ({ fetchWorkspaces: vi.fn() }))

function renderPage(path = '/workspaces/ws1/resources') {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/resources"
            element={<ResourceLibraryPage />}
          />
          <Route
            path="/workspaces/:workspaceId/workflow-studio"
            element={<div>Studio destination</div>}
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('ResourceLibraryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchWorkspaces).mockResolvedValue({
      workspaces: [makeWorkspace({ id: 'ws1', name: '数学教研' })],
    })
    vi.mocked(listSharedResources).mockResolvedValue({
      resources: [makeSharedResource()],
      total: 1,
      limit: 200,
      offset: 0,
    })
    useAuthStore.setState({
      user: { id: 'admin', username: 'admin', role: 'admin' } as never,
      status: 'authenticated',
    })
  })

  it('首屏只显示名称、来源和主行动，不显示版本或技术详情', async () => {
    renderPage()
    expect(await screen.findByText('通用审核规则')).toBeInTheDocument()
    expect(screen.getByText('来自 数学教研')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加到工作流' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '共享' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '管理' })).toBeInTheDocument()
    expect(screen.queryByText(/v1/)).not.toBeInTheDocument()
    expect(screen.queryByText(/manifest|hash|sha256/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/job-/)).not.toBeInTheDocument()
  })

  it('主行动进入真实 Workflow Studio 资源选择路由', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: '添加到工作流' })
    )
    expect(await screen.findByText('Studio destination')).toBeInTheDocument()
  })

  it('空态、权限错误和加载错误都有可理解反馈', async () => {
    vi.mocked(listSharedResources).mockResolvedValueOnce({
      resources: [],
      total: 0,
      limit: 200,
      offset: 0,
    })
    const first = renderPage()
    expect(await screen.findByText('资源库还是空的')).toBeInTheDocument()
    first.unmount()

    vi.mocked(listSharedResources).mockRejectedValueOnce(
      Object.assign(new Error('forbidden'), { status: 403 })
    )
    renderPage()
    expect(
      await screen.findByText('无法查看这个工作区的资源')
    ).toBeInTheDocument()
    expect(
      screen.getByText('你没有查看权限，或工作区已经不存在。')
    ).toBeInTheDocument()
  })

  it('来源 viewer 可共享到目标 editor，但不能撤销已有共享', async () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'viewer', role: 'member' } as never,
      status: 'authenticated',
    })
    vi.mocked(fetchWorkspaces).mockResolvedValue({
      workspaces: [
        makeWorkspace({
          id: 'ws1',
          name: '数学教研',
          current_user_role: 'viewer',
        }),
        makeWorkspace({
          id: 'ws2',
          name: '试题维护',
          current_user_role: 'editor',
        }),
      ],
    })
    const user = userEvent.setup()
    renderPage()
    expect(
      await screen.findByText(/你在当前工作区为只读成员/)
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加到工作流' })).toBeDisabled()
    vi.mocked(shareSharedResource).mockResolvedValue({
      duplicate: false,
      grant: { workspace_id: 'ws2', workspace_name: '试题维护' } as never,
    })
    await user.click(screen.getByRole('button', { name: '共享' }))
    expect(
      await screen.findByText(
        /可以共享到你有编辑权限的其他工作区，不能撤销已有共享/
      )
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '共享到试题维护' }))
    expect(shareSharedResource).toHaveBeenCalledWith('ws1', 'resource-1', 'ws2')
    expect(
      screen.queryByRole('button', { name: '停止新任务使用' })
    ).not.toBeInTheDocument()
  })

  it('停止新任务使用后主行动不可用', async () => {
    vi.mocked(listSharedResources).mockResolvedValue({
      resources: [makeSharedResource({ access_status: 'revoked' })],
      total: 1,
      limit: 200,
      offset: 0,
    })
    renderPage()
    expect(await screen.findByText('已停止新任务使用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加到工作流' })).toBeDisabled()
  })
})
