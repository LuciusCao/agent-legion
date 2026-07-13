import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './core'
import {
  deletePackage,
  deleteWorkspacePackage,
  fetchPackages,
  fetchWorkspacePackages,
  updatePackage,
  updateWorkspacePackage,
} from './packages'

vi.mock('./core', () => ({ api: vi.fn() }))

const mockApi = vi.mocked(api)

beforeEach(() => {
  mockApi.mockReset()
})

describe('package api', () => {
  it('fetches global and workspace package collections', async () => {
    mockApi.mockResolvedValue({ packages: [] })

    await fetchPackages()
    await fetchWorkspacePackages('workspace/one')

    expect(mockApi).toHaveBeenNthCalledWith(1, '/api/packages')
    expect(mockApi).toHaveBeenNthCalledWith(
      2,
      '/api/workspaces/workspace%2Fone/packages'
    )
  })

  it('deletes global and workspace packages', async () => {
    mockApi.mockResolvedValue({ deleted: true })

    await deletePackage(12)
    await deleteWorkspacePackage('workspace/one', 34)

    expect(mockApi).toHaveBeenNthCalledWith(1, '/api/packages/12', {
      method: 'DELETE',
    })
    expect(mockApi).toHaveBeenNthCalledWith(
      2,
      '/api/workspaces/workspace%2Fone/packages/34',
      { method: 'DELETE' }
    )
  })

  it('patches global and workspace packages', async () => {
    mockApi.mockResolvedValue({ id: 12 })

    await updatePackage(12, { name: 'Review', locked: true })
    await updateWorkspacePackage('workspace/one', 34, { locked: false })

    expect(mockApi).toHaveBeenNthCalledWith(1, '/api/packages/12', {
      method: 'PATCH',
      body: JSON.stringify({ name: 'Review', locked: true }),
    })
    expect(mockApi).toHaveBeenNthCalledWith(
      2,
      '/api/workspaces/workspace%2Fone/packages/34',
      {
        method: 'PATCH',
        body: JSON.stringify({ locked: false }),
      }
    )
  })
})
