import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import LoginPage from './LoginPage'

const { login } = vi.hoisted(() => ({ login: vi.fn() }))

vi.mock('../stores/authStore', () => ({
  useAuthStore: (selector: (state: { login: typeof login }) => unknown) =>
    selector({ login }),
}))

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>dashboard home</div>} />
      </Routes>
    </MemoryRouter>
  )
}

async function fillCredentials(username = ' admin ', password = 'secret') {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('用户名'), username)
  await user.type(screen.getByLabelText('密码'), password)
  return user
}

beforeEach(() => {
  login.mockReset()
})

describe('LoginPage', () => {
  it('keeps submission disabled until both credentials are present', async () => {
    renderLogin()
    const submit = screen.getByRole('button', { name: '登录' })

    expect(submit).toBeDisabled()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('用户名'), 'admin')
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('密码'), 'secret')
    expect(submit).toBeEnabled()
  })

  it('trims the username, logs in, and replaces the login route', async () => {
    login.mockResolvedValue(undefined)
    renderLogin()
    const user = await fillCredentials()

    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(login).toHaveBeenCalledWith('admin', 'secret')
    expect(await screen.findByText('dashboard home')).toBeInTheDocument()
  })

  it.each([
    [401, '用户名或密码错误'],
    [429, '失败次数过多，请稍后再试'],
  ])('maps HTTP %s failures to a safe message', async (status, message) => {
    login.mockRejectedValue(
      Object.assign(new Error('server detail'), { status })
    )
    renderLogin()
    const user = await fillCredentials('admin')

    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.getByRole('button', { name: '登录' })).toBeEnabled()
  })

  it('shows a generic login failure and allows retrying', async () => {
    login.mockRejectedValue(new Error('network unavailable'))
    renderLogin()
    const user = await fillCredentials('admin')

    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'network unavailable'
    )
    expect(screen.getByRole('button', { name: '登录' })).toBeEnabled()
  })
})
