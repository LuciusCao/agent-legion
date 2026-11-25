import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeWorkspace } from '../../testing/workspaceFixtures'
import { makeSharedResource } from '../../testing/sharedResourceFixtures'
import { fetchWorkspaces } from '../../api'
import {
  listSharedResourceGrants,
  revokeSharedResource,
  shareSharedResource,
} from '../../api/sharedResources'
import { useAuthStore } from '../../stores/authStore'
import { ShareResourceDialog } from './ShareResourceDialog'

vi.mock('../../api', () => ({ fetchWorkspaces: vi.fn() }))
vi.mock('../../api/sharedResources', () => ({
  listSharedResourceGrants: vi.fn(),
  shareSharedResource: vi.fn(),
  revokeSharedResource: vi.fn(),
}))

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function renderDialog() {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={['/workspaces/ws1/resources']}>
        <LocationProbe />
        <Routes>
          <Route
            path="/workspaces/:workspaceId/resources"
            element={
              <ShareResourceDialog
                workspaceId="ws1"
                resource={makeSharedResource()}
                canEdit
                onClose={vi.fn()}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('ShareResourceDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({
      user: { id: 'u1', username: 'editor', role: 'member' } as never,
      status: 'authenticated',
    })
    vi.mocked(fetchWorkspaces).mockResolvedValue({
      workspaces: [
        makeWorkspace({ id: 'ws1', name: '数学教研' }),
        makeWorkspace({ id: 'ws2', name: '试题维护' }),
        makeWorkspace({
          id: 'ws3',
          name: '只读工作区',
          current_user_role: 'viewer',
        }),
      ],
    })
    vi.mocked(listSharedResourceGrants).mockResolvedValue({ grants: [] })
  })

  it('只列出可编辑目标，首次授权和重复共享显示不同回执', async () => {
    const user = userEvent.setup()
    vi.mocked(shareSharedResource)
      .mockResolvedValueOnce({
        duplicate: false,
        grant: { workspace_id: 'ws2', workspace_name: '试题维护' } as never,
      })
      .mockResolvedValueOnce({
        duplicate: true,
        grant: { workspace_id: 'ws2', workspace_name: '试题维护' } as never,
      })
    renderDialog()
    const target = await screen.findByLabelText('目标工作区')
    await waitFor(() => expect(target).toHaveValue('ws2'))
    expect(screen.getByRole('option', { name: '试题维护' })).toBeInTheDocument()
    expect(
      screen.queryByRole('option', { name: '只读工作区' })
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '共享到试题维护' }))
    expect(await screen.findByText('已共享到试题维护。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '共享到试题维护' }))
    expect(
      await screen.findByText('此前已共享到试题维护，无需重复操作。')
    ).toBeInTheDocument()
    expect(shareSharedResource).toHaveBeenNthCalledWith(
      2,
      'ws1',
      'resource-1',
      'ws2'
    )

    await user.click(screen.getByRole('button', { name: '切换到试题维护' }))
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/workspaces/ws2/resources'
    )
  })

  it('停止新任务使用后清除旧的切换回执', async () => {
    const user = userEvent.setup()
    vi.mocked(shareSharedResource).mockResolvedValue({
      duplicate: false,
      grant: { workspace_id: 'ws2', workspace_name: '试题维护' } as never,
    })
    vi.mocked(listSharedResourceGrants).mockResolvedValue({
      grants: [
        {
          id: 'grant-1',
          workspace_id: 'ws2',
          workspace_name: '试题维护',
          status: 'active',
        } as never,
      ],
    })
    vi.mocked(revokeSharedResource).mockResolvedValue({
      grant: { workspace_id: 'ws2', status: 'revoked' } as never,
    })
    renderDialog()
    await user.click(
      await screen.findByRole('button', { name: '共享到试题维护' })
    )
    expect(
      await screen.findByRole('button', { name: '切换到试题维护' })
    ).toBeInTheDocument()
    vi.mocked(listSharedResourceGrants).mockResolvedValue({
      grants: [
        {
          id: 'grant-1',
          workspace_id: 'ws2',
          workspace_name: '试题维护',
          status: 'revoked',
        } as never,
      ],
    })
    await user.click(screen.getByRole('button', { name: '停止新任务使用' }))
    expect(
      await screen.findByText('已停止新任务使用，已有任务不受影响')
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '切换到试题维护' })
    ).not.toBeInTheDocument()
  })

  it('共享失败不显示成功回执或切换按钮', async () => {
    const user = userEvent.setup()
    vi.mocked(shareSharedResource).mockRejectedValue(
      Object.assign(new Error('forbidden'), { status: 403 })
    )
    renderDialog()
    await user.click(
      await screen.findByRole('button', { name: '共享到试题维护' })
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '你没有权限执行这个操作'
    )
    expect(
      screen.queryByRole('button', { name: /切换到/ })
    ).not.toBeInTheDocument()
  })
})
