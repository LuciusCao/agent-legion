import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from './authStore'
import {
  fetchBootstrapStatus,
  fetchMe,
  login as apiLogin,
} from '../api/authApi'
import type { UserResponse } from '../api/authApi'

vi.mock('../api/authApi', () => ({
  fetchBootstrapStatus: vi.fn(),
  fetchMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  bootstrap: vi.fn(),
}))

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: 'Admin',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const unauthorized = Object.assign(new Error('Not authenticated'), {
  status: 401,
})

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    status: 'unknown',
    bootstrapAvailable: null,
  })
  vi.clearAllMocks()
})

describe('initialize', () => {
  it('marks bootstrap available when the system has no users', async () => {
    vi.mocked(fetchBootstrapStatus).mockResolvedValue({ available: true })
    vi.mocked(fetchMe).mockRejectedValue(unauthorized)

    await useAuthStore.getState().initialize()

    const state = useAuthStore.getState()
    expect(state.bootstrapAvailable).toBe(true)
    expect(state.user).toBeNull()
    expect(state.status).toBe('anonymous')
  })

  it('authenticates when /me returns a user', async () => {
    vi.mocked(fetchBootstrapStatus).mockResolvedValue({ available: false })
    vi.mocked(fetchMe).mockResolvedValue(adminUser)

    await useAuthStore.getState().initialize()

    const state = useAuthStore.getState()
    expect(state.bootstrapAvailable).toBe(false)
    expect(state.user).toEqual(adminUser)
    expect(state.status).toBe('authenticated')
  })

  it('stays anonymous without a session', async () => {
    vi.mocked(fetchBootstrapStatus).mockResolvedValue({ available: false })
    vi.mocked(fetchMe).mockRejectedValue(unauthorized)

    await useAuthStore.getState().initialize()

    const state = useAuthStore.getState()
    expect(state.bootstrapAvailable).toBe(false)
    expect(state.user).toBeNull()
    expect(state.status).toBe('anonymous')
  })
})

describe('login', () => {
  it('sets the user and marks authenticated on success', async () => {
    vi.mocked(apiLogin).mockResolvedValue(adminUser)

    await useAuthStore.getState().login('admin', 'secret')

    const state = useAuthStore.getState()
    expect(state.user).toEqual(adminUser)
    expect(state.status).toBe('authenticated')
    expect(apiLogin).toHaveBeenCalledWith({
      username: 'admin',
      password: 'secret',
    })
  })

  it('propagates failures and stays anonymous', async () => {
    useAuthStore.setState({ status: 'anonymous' })
    vi.mocked(apiLogin).mockRejectedValue(
      Object.assign(new Error('Invalid username or password'), { status: 401 })
    )

    await expect(
      useAuthStore.getState().login('admin', 'wrong')
    ).rejects.toThrow('Invalid username or password')

    const state = useAuthStore.getState()
    expect(state.user).toBeNull()
    expect(state.status).toBe('anonymous')
  })
})
