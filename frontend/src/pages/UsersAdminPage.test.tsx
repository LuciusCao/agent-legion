import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import UsersAdminPage from './UsersAdminPage'
import { useAuthStore } from '../stores/authStore'
import { createUser, listUsers } from '../api/authApi'
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
})
