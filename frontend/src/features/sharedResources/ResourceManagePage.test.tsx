import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeWorkspace } from '../../testing/workspaceFixtures'
import {
  makeSharedResource,
  makeSharedResourceVersion,
} from '../../testing/sharedResourceFixtures'
import { fetchWorkspaces } from '../../api'
import {
  deleteSharedResourceVersion,
  getSharedResource,
  listSharedResourceVersions,
  selectSharedResourceVersion,
} from '../../api/sharedResources'
import { useAuthStore } from '../../stores/authStore'
import ResourceManagePage from './ResourceManagePage'

vi.mock('../../api', () => ({ fetchWorkspaces: vi.fn() }))
vi.mock('../../api/sharedResources', () => ({
  getSharedResource: vi.fn(),
  listSharedResourceVersions: vi.fn(),
  selectSharedResourceVersion: vi.fn(),
  deleteSharedResourceVersion: vi.fn(),
  listSharedResourceGrants: vi.fn(),
  shareSharedResource: vi.fn(),
  revokeSharedResource: vi.fn(),
}))
vi.mock('./ResourceVersionDiff', () => ({
  ResourceVersionDiff: () => <div>文件差异测试占位</div>,
}))
vi.mock('./ResourceJobHistory', () => ({
  ResourceJobHistory: () => <div>任务版本测试占位</div>,
}))

const v1 = makeSharedResourceVersion()
const v2 = makeSharedResourceVersion({
  id: 'version-2',
  version: 2,
  manifest_hash: 'b'.repeat(64),
})

function renderPage() {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={['/workspaces/ws1/resources/resource-1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/resources/:resourceId"
            element={<ResourceManagePage />}
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('ResourceManagePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchWorkspaces).mockResolvedValue({
      workspaces: [makeWorkspace()],
    })
    vi.mocked(getSharedResource).mockResolvedValue({
      resource: makeSharedResource(),
    })
    vi.mocked(listSharedResourceVersions).mockResolvedValue({
      versions: [v2, v1],
    })
    useAuthStore.setState({
      user: { id: 'admin', username: 'admin', role: 'admin' } as never,
      status: 'authenticated',
    })
  })

  it('升级携带当前修订号，并明确只影响新任务', async () => {
    const user = userEvent.setup()
    vi.mocked(selectSharedResourceVersion).mockResolvedValue({
      selection: { version: 2 } as never,
    })
    renderPage()
    expect(await screen.findByText('当前使用版本：v1')).toBeInTheDocument()
    expect(screen.getByText(/切换版本只影响之后创建的任务/)).toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: '升级到 v2（仅新任务使用）' })
    )
    await waitFor(() =>
      expect(selectSharedResourceVersion).toHaveBeenCalledWith(
        'ws1',
        'resource-1',
        {
          version: 2,
          expectedRevision: 3,
        }
      )
    )
    expect(
      await screen.findByText('之后创建的任务将使用 v2，已有任务不变。')
    ).toBeInTheDocument()
  })

  it('viewer 只能查看，版本写操作不可用', async () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'viewer', role: 'member' } as never,
      status: 'authenticated',
    })
    vi.mocked(fetchWorkspaces).mockResolvedValue({
      workspaces: [makeWorkspace({ current_user_role: 'viewer' })],
    })
    renderPage()
    expect(
      await screen.findByRole('button', { name: '升级到 v2（仅新任务使用）' })
    ).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: '创建资源版本' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '删除版本' })
    ).not.toBeInTheDocument()
  })

  it('删除保护转换为普通中文，且不改写历史任务', async () => {
    const user = userEvent.setup()
    vi.mocked(deleteSharedResourceVersion).mockRejectedValue(
      Object.assign(new Error('Shared resource version still has references'), {
        status: 409,
      })
    )
    renderPage()
    await user.click(
      (await screen.findAllByRole('button', { name: '删除版本' }))[0]
    )
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '这个版本仍被工作区或任务使用，不能删除。已有任务会继续保留当时使用的版本。'
    )
  })
})
