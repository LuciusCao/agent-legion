import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { useAuthStore } from '../stores/authStore'
import { UserMenu } from './UserMenu'

function LocationProbe() {
  return <span data-testid="location-path">{useLocation().pathname}</span>
}

const adminUser = {
  id: 'u1',
  username: 'admin',
  display_name: 'Admin',
  role: 'admin' as const,
  disabled: false,
  created_at: '2026-08-01T00:00:00Z',
  disabled_at: null,
}

const memberUser = {
  ...adminUser,
  id: 'u2',
  username: 'alice',
  role: 'member' as const,
}

function renderMenu() {
  return render(
    <MemoryRouter>
      <UserMenu />
    </MemoryRouter>
  )
}

describe('UserMenu', () => {
  it('shows admin entries including the global monitoring entry for admins', () => {
    useAuthStore.setState({ user: adminUser })

    renderMenu()

    expect(screen.getByRole('button', { name: '监控面板' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '设置' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '用户管理' })).toBeInTheDocument()
  })

  it('hides admin entries from members', () => {
    useAuthStore.setState({ user: memberUser })

    renderMenu()

    expect(
      screen.queryByRole('button', { name: '监控面板' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '设置' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '退出登录' })).toBeInTheDocument()
  })

  it('navigates to the global monitoring page from the entry', async () => {
    useAuthStore.setState({ user: adminUser })
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <UserMenu />
        <LocationProbe />
      </MemoryRouter>
    )
    await user.click(screen.getByRole('button', { name: '监控面板' }))

    expect(screen.getByTestId('location-path')).toHaveTextContent('/monitoring')
  })
})
