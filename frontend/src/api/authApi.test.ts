import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  bootstrap,
  createUser,
  fetchBootstrapStatus,
  fetchMe,
  listMembers,
  listUsers,
  login,
  logout,
  putMember,
  removeMember,
  updateUser,
} from './authApi'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

describe('auth api', () => {
  it('logs in and unwraps the user', async () => {
    const user = { id: 'u1', username: 'admin' }
    const fetchMock = mockFetchJson({ user })
    global.fetch = fetchMock

    const result = await login({ username: 'admin', password: 'pw' })

    expect(result).toEqual(user)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ username: 'admin', password: 'pw' }),
      })
    )
  })

  it('logs out with a POST', async () => {
    const fetchMock = mockFetchJson({ user: null })
    global.fetch = fetchMock

    await logout()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/logout',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('fetches the current user', async () => {
    const user = { id: 'u1', username: 'admin' }
    const fetchMock = mockFetchJson({ user })
    global.fetch = fetchMock

    const result = await fetchMe()

    expect(result).toEqual(user)
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.anything())
  })

  it('fetches bootstrap status', async () => {
    const status = { needs_bootstrap: true }
    const fetchMock = mockFetchJson(status)
    global.fetch = fetchMock

    const result = await fetchBootstrapStatus()

    expect(result).toEqual(status)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/bootstrap',
      expect.anything()
    )
  })

  it('bootstraps the first admin and unwraps the user', async () => {
    const user = { id: 'u1', username: 'admin' }
    const fetchMock = mockFetchJson({ user })
    global.fetch = fetchMock

    const input = {
      username: 'admin',
      password: 'pw',
      display_name: 'Admin',
      role: 'admin',
    } as const
    const result = await bootstrap(input)

    expect(result).toEqual(user)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/bootstrap',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(input),
      })
    )
  })

  it('lists users and defaults to an empty list', async () => {
    const fetchMock = mockFetchJson({ users: [{ id: 'u1' }] })
    global.fetch = fetchMock
    expect(await listUsers()).toEqual([{ id: 'u1' }])

    global.fetch = mockFetchJson({})
    expect(await listUsers()).toEqual([])
  })

  it('creates a user', async () => {
    const user = { id: 'u2', username: 'alice' }
    const fetchMock = mockFetchJson(user)
    global.fetch = fetchMock

    const result = await createUser({
      username: 'alice',
      password: 'pw',
      display_name: 'Alice',
      role: 'member',
    })

    expect(result).toEqual(user)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/users',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          username: 'alice',
          password: 'pw',
          display_name: 'Alice',
          role: 'member',
        }),
      })
    )
  })

  it('patches a user with an encoded id', async () => {
    const user = { id: 'u/1', username: 'alice' }
    const fetchMock = mockFetchJson(user)
    global.fetch = fetchMock

    const result = await updateUser('u/1', { display_name: 'Alice' })

    expect(result).toEqual(user)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/users/u%2F1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ display_name: 'Alice' }),
      })
    )
  })

  it('lists members and defaults to an empty list', async () => {
    const fetchMock = mockFetchJson({ members: [{ user_id: 'u1' }] })
    global.fetch = fetchMock
    expect(await listMembers('ws 1')).toEqual([{ user_id: 'u1' }])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/members',
      expect.anything()
    )

    global.fetch = mockFetchJson({})
    expect(await listMembers('ws 1')).toEqual([])
  })

  it('puts a member and unwraps the member list', async () => {
    const fetchMock = mockFetchJson({
      members: [{ user_id: 'u1', role: 'editor' }],
    })
    global.fetch = fetchMock

    const result = await putMember('ws1', { user_id: 'u1', role: 'editor' })

    expect(result).toEqual([{ user_id: 'u1', role: 'editor' }])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/members',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ user_id: 'u1', role: 'editor' }),
      })
    )

    global.fetch = mockFetchJson({})
    expect(await putMember('ws1', { user_id: 'u1', role: 'editor' })).toEqual(
      []
    )
  })

  it('removes a member with encoded ids', async () => {
    const fetchMock = mockFetchJson({ members: [] })
    global.fetch = fetchMock

    const result = await removeMember('ws 1', 'u/1')

    expect(result).toEqual([])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/members/u%2F1',
      expect.objectContaining({ method: 'DELETE' })
    )

    global.fetch = mockFetchJson({})
    expect(await removeMember('ws 1', 'u/1')).toEqual([])
  })
})
