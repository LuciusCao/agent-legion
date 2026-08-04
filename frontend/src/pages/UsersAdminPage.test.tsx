import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import UsersAdminPage from './UsersAdminPage'
import { useAuthStore } from '../stores/authStore'
import { createUser, listUsers, updateUser } from '../api/authApi'
import type { UserResponse } from '../api/authApi'

vi.mock('../api/authApi', () => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
}))

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: '管理员',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const memberUser: UserResponse = {
  id: 'u2',
  username: 'alice',
  display_name: 'Alice',
  role: 'member',
  disabled_at: null,
  created_at: '2026-01-02T00:00:00Z',
}

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersAdminPage />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({
    user: adminUser,
    status: 'authenticated',
    bootstrapAvailable: false,
  })
})

describe('UsersAdminPage', () => {
  it('renders the user list', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser, memberUser])

    renderPage()

    expect(await screen.findByText(/alice/)).toBeInTheDocument()
    expect(screen.getByText(/admin/)).toBeInTheDocument()
  })

  it('creates a user and refreshes the list', async () => {
    vi.mocked(listUsers)
      .mockResolvedValueOnce([adminUser])
      .mockResolvedValueOnce([adminUser, memberUser])
    vi.mocked(createUser).mockResolvedValue(memberUser)

    renderPage()
    await screen.findByTestId('user-u1')

    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByText('创建'))

    await waitFor(() => {
      expect(createUser).toHaveBeenCalledWith({
        username: 'alice',
        password: 'secret',
        display_name: '',
        role: 'member',
      })
    })
    expect(await screen.findByText(/alice/)).toBeInTheDocument()
  })

  it('shows a no-permission hint for non-admin users', () => {
    useAuthStore.setState({ user: memberUser })

    renderPage()

    expect(screen.getByText(/无权限访问/)).toBeInTheDocument()
    expect(listUsers).not.toHaveBeenCalled()
  })

  it('shows an error when the user list fails to load', async () => {
    vi.mocked(listUsers).mockRejectedValue(new Error('load failed'))

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('load failed')
  })

  it('shows an error when creating a user fails', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser])
    vi.mocked(createUser).mockRejectedValue(new Error('create failed'))

    renderPage()
    await screen.findByTestId('user-u1')
    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByText('创建'))

    expect(await screen.findByRole('alert')).toHaveTextContent('create failed')
  })

  it('stringifies non-error failures', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser])
    vi.mocked(createUser).mockRejectedValue('plain failure')

    renderPage()
    await screen.findByTestId('user-u1')
    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'alice' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByText('创建'))

    expect(await screen.findByRole('alert')).toHaveTextContent('plain failure')
  })

  it('creates a user with display name and admin role', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser])
    vi.mocked(createUser).mockResolvedValue(memberUser)

    renderPage()
    await screen.findByTestId('user-u1')
    fireEvent.change(screen.getByLabelText('用户名'), {
      target: { value: 'bob' },
    })
    fireEvent.change(screen.getByLabelText('显示名'), {
      target: { value: 'Bob' },
    })
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret' },
    })
    fireEvent.change(screen.getByLabelText('角色'), {
      target: { value: 'admin' },
    })
    fireEvent.click(screen.getByText('创建'))

    await waitFor(() => {
      expect(createUser).toHaveBeenCalledWith({
        username: 'bob',
        password: 'secret',
        display_name: 'Bob',
        role: 'admin',
      })
    })
  })

  it('toggles a member to admin and refreshes the list', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser, memberUser])
    vi.mocked(updateUser).mockResolvedValue(memberUser)

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '设为管理员' }))

    await waitFor(() => {
      expect(updateUser).toHaveBeenCalledWith('u2', { role: 'admin' })
    })
    expect(listUsers).toHaveBeenCalledTimes(2)
  })

  it('shows an error when toggling a role fails', async () => {
    vi.mocked(listUsers).mockResolvedValue([adminUser, memberUser])
    vi.mocked(updateUser).mockRejectedValue(new Error('toggle failed'))

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '设为管理员' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('toggle failed')
  })

  it('disables an active user and re-enables a disabled one', async () => {
    const disabledUser: UserResponse = {
      ...memberUser,
      id: 'u3',
      username: 'carol',
      disabled_at: '2026-02-01T00:00:00Z',
    }
    vi.mocked(listUsers).mockResolvedValue([memberUser, disabledUser])
    vi.mocked(updateUser).mockResolvedValue(memberUser)

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '禁用' }))
    await waitFor(() => {
      expect(updateUser).toHaveBeenCalledWith('u2', { disabled: true })
    })

    fireEvent.click(screen.getByRole('button', { name: '启用' }))
    await waitFor(() => {
      expect(updateUser).toHaveBeenCalledWith('u3', { disabled: false })
    })
  })

  it('shows an error when toggling disabled fails', async () => {
    vi.mocked(listUsers).mockResolvedValue([memberUser])
    vi.mocked(updateUser).mockRejectedValue(new Error('disable failed'))

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '禁用' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('disable failed')
  })

  it('resets a password through the prompt', async () => {
    vi.mocked(listUsers).mockResolvedValue([memberUser])
    vi.mocked(updateUser).mockResolvedValue(memberUser)
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('new-pw')

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '重置密码' }))

    await waitFor(() => {
      expect(updateUser).toHaveBeenCalledWith('u2', { password: 'new-pw' })
    })
    promptSpy.mockRestore()
  })

  it('does nothing when the password prompt is cancelled', async () => {
    vi.mocked(listUsers).mockResolvedValue([memberUser])
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue(null)

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '重置密码' }))

    expect(updateUser).not.toHaveBeenCalled()
    promptSpy.mockRestore()
  })

  it('shows an error when resetting a password fails', async () => {
    vi.mocked(listUsers).mockResolvedValue([memberUser])
    vi.mocked(updateUser).mockRejectedValue(new Error('reset failed'))
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('new-pw')

    renderPage()
    await screen.findByTestId('user-u2')
    fireEvent.click(screen.getByRole('button', { name: '重置密码' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('reset failed')
    promptSpy.mockRestore()
  })
})
