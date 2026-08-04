import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import SetupPage from './SetupPage'

const { bootstrap } = vi.hoisted(() => ({ bootstrap: vi.fn() }))

vi.mock('../stores/authStore', () => ({
  useAuthStore: (
    selector: (state: { bootstrap: typeof bootstrap }) => unknown
  ) => selector({ bootstrap }),
}))

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={['/setup']}>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/" element={<div>dashboard home</div>} />
      </Routes>
    </MemoryRouter>
  )
}

async function fillSetup(options?: {
  username?: string
  displayName?: string
  password?: string
  confirmation?: string
}) {
  const {
    username = ' admin ',
    displayName = ' Administrator ',
    password = 'secret',
    confirmation = password,
  } = options ?? {}
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('用户名'), username)
  if (displayName) {
    await user.type(screen.getByLabelText('显示名（可选）'), displayName)
  }
  await user.type(screen.getByLabelText('密码'), password)
  await user.type(screen.getByLabelText('确认密码'), confirmation)
  return user
}

beforeEach(() => {
  bootstrap.mockReset()
})

describe('SetupPage', () => {
  it('rejects mismatched passwords without calling bootstrap', async () => {
    renderSetup()
    const user = await fillSetup({ confirmation: 'different' })

    await user.click(screen.getByRole('button', { name: '创建并登录' }))

    expect(screen.getByRole('alert')).toHaveTextContent('两次输入的密码不一致')
    expect(bootstrap).not.toHaveBeenCalled()
  })

  it('creates the administrator with trimmed names and navigates home', async () => {
    bootstrap.mockResolvedValue(undefined)
    renderSetup()
    const user = await fillSetup()

    await user.click(screen.getByRole('button', { name: '创建并登录' }))

    expect(bootstrap).toHaveBeenCalledWith('admin', 'secret', 'Administrator')
    expect(await screen.findByText('dashboard home')).toBeInTheDocument()
  })

  it('allows an empty optional display name', async () => {
    bootstrap.mockResolvedValue(undefined)
    renderSetup()
    const user = await fillSetup({ displayName: '' })

    await user.click(screen.getByRole('button', { name: '创建并登录' }))

    expect(bootstrap).toHaveBeenCalledWith('admin', 'secret', '')
  })

  it('shows bootstrap failures and re-enables submission', async () => {
    bootstrap.mockRejectedValue(new Error('bootstrap unavailable'))
    renderSetup()
    const user = await fillSetup()

    await user.click(screen.getByRole('button', { name: '创建并登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'bootstrap unavailable'
    )
    expect(screen.getByRole('button', { name: '创建并登录' })).toBeEnabled()
  })
})
