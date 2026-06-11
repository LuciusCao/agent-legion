import { describe, it, expect, vi, beforeEach } from 'vitest'
import { usePackageStore, type PackageItem } from './packageStore'
import { api, fetchPackages } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchPackages: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchPackages = vi.mocked(fetchPackages)

const pkgA: PackageItem = {
  id: 1,
  name: 'Package A',
  path: '/data/packages/a.zip',
  video_count: 2,
  size_bytes: 1024,
  locked: 0,
  created_at: '2024-01-01T00:00:00Z',
}

const pkgB: PackageItem = {
  id: 2,
  name: 'Package B',
  path: '/data/packages/b.zip',
  video_count: 3,
  size_bytes: 2048,
  locked: 1,
  created_at: '2024-01-02T00:00:00Z',
}

describe('packageStore', () => {
  beforeEach(() => {
    usePackageStore.setState({
      packages: [],
      history: [],
      selectedIds: [],
      loading: false,
      error: null,
    })
    mockApi.mockReset()
    mockFetchPackages.mockReset()
  })

  it('toggles selection to add and remove package IDs', () => {
    usePackageStore.getState().toggleSelection(1)
    expect(usePackageStore.getState().selectedIds).toContain(1)

    usePackageStore.getState().toggleSelection(1)
    expect(usePackageStore.getState().selectedIds).not.toContain(1)
  })

  it('selects all visible packages and clears the selection', () => {
    usePackageStore.setState({ packages: [pkgA, pkgB] })

    usePackageStore.getState().selectAll()
    expect(usePackageStore.getState().selectedIds).toEqual([1, 2])

    usePackageStore.getState().clearSelection()
    expect(usePackageStore.getState().selectedIds).toEqual([])
  })

  it('returns the correct download URL for a package ID', () => {
    expect(usePackageStore.getState().downloadUrlFor(42)).toBe(
      '/api/packages/42/download'
    )
  })

  it('populates packages via fetchPackages', async () => {
    mockFetchPackages.mockResolvedValueOnce({ packages: [pkgA] })

    await usePackageStore.getState().fetchPackages()

    const state = usePackageStore.getState()
    expect(state.packages).toEqual([pkgA])
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('sets error state when fetchPackages fails', async () => {
    mockFetchPackages.mockRejectedValueOnce(new Error('network error'))

    await usePackageStore.getState().fetchPackages()

    const state = usePackageStore.getState()
    expect(state.packages).toEqual([])
    expect(state.error).toBe('network error')
    expect(state.loading).toBe(false)
  })

  it('populates history via fetchHistory', async () => {
    mockApi.mockResolvedValueOnce({ packages: [pkgB] })

    await usePackageStore.getState().fetchHistory()

    const state = usePackageStore.getState()
    expect(state.history).toEqual([pkgB])
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('sets error state when fetchHistory fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('history unavailable'))

    await usePackageStore.getState().fetchHistory()

    const state = usePackageStore.getState()
    expect(state.history).toEqual([])
    expect(state.error).toBe('history unavailable')
    expect(state.loading).toBe(false)
  })
})
