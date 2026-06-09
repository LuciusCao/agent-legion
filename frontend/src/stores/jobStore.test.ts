import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useJobStore } from './jobStore'
import type { JobRecord } from '../types'

vi.mock('../api', () => ({
  fetchJobs: vi.fn(),
  api: vi.fn(),
}))

import { fetchJobs, api } from '../api'

const mockFetchJobs = vi.mocked(fetchJobs)
const mockApi = vi.mocked(api)

describe('jobStore', () => {
  beforeEach(() => {
    useJobStore.setState({
      jobs: [],
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      expandedId: null,
      statusFilter: 'all',
      searchQuery: '',
    })
    mockFetchJobs.mockReset()
    mockApi.mockReset()
  })

  it('toggles selection', () => {
    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().selectedIds.has('j1')).toBe(true)
    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().selectedIds.has('j1')).toBe(false)
  })

  it('selects all visible jobs', () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          status: 'pending',
          source_id: 'Q1',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
        {
          id: 'j2',
          status: 'completed',
          source_id: 'Q2',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
      ] as JobRecord[],
    })
    useJobStore.getState().selectAll()
    expect(useJobStore.getState().selectedIds.size).toBe(2)
  })

  it('filters by status', () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          status: 'pending',
          source_id: 'Q1',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
        {
          id: 'j2',
          status: 'completed',
          source_id: 'Q2',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
      ] as JobRecord[],
    })
    useJobStore.getState().setStatusFilter('completed')
    const filtered = useJobStore.getState().getFilteredJobs()
    expect(filtered).toHaveLength(1)
    expect(filtered[0].id).toBe('j2')
  })

  it('filters by search query', () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          status: 'pending',
          source_id: 'Q100',
          title: 'Algebra',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
        {
          id: 'j2',
          status: 'completed',
          source_id: 'Q200',
          title: 'Geometry',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
      ] as JobRecord[],
    })
    useJobStore.getState().setSearchQuery('Q100')
    const filtered = useJobStore.getState().getFilteredJobs()
    expect(filtered).toHaveLength(1)
    expect(filtered[0].id).toBe('j1')
  })

  it('fetches jobs and sets state on success', async () => {
    const jobs: JobRecord[] = [
      {
        id: 'j1',
        status: 'pending',
        source_id: 'Q1',
        title: 'One',
        workspace_id: 'ws1',
        pipeline_key: 'p1',
      },
    ]
    mockFetchJobs.mockResolvedValueOnce({ jobs })

    await useJobStore.getState().fetchJobs('ws1')

    expect(useJobStore.getState().jobs).toEqual(jobs)
    expect(useJobStore.getState().isLoading).toBe(false)
    expect(useJobStore.getState().error).toBeNull()
    expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
  })

  it('sets error on fetch failure', async () => {
    mockFetchJobs.mockRejectedValueOnce(new Error('network down'))

    await useJobStore.getState().fetchJobs('ws1')

    expect(useJobStore.getState().error).toBe('network down')
    expect(useJobStore.getState().isLoading).toBe(false)
  })

  it('clears selection when filter changes', () => {
    useJobStore.setState({ selectedIds: new Set(['j1', 'j2']) })
    useJobStore.getState().setStatusFilter('completed')
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('clears selection when search query changes', () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    useJobStore.getState().setSearchQuery('foo')
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('toggles expand between id and null', () => {
    useJobStore.getState().toggleExpand('j1')
    expect(useJobStore.getState().expandedId).toBe('j1')
    useJobStore.getState().toggleExpand('j1')
    expect(useJobStore.getState().expandedId).toBeNull()
  })

  it('calls batch rerun endpoint and clears selection on success', async () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          status: 'failed',
          source_id: 'Q1',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
      ] as JobRecord[],
      selectedIds: new Set(['j1']),
    })
    mockApi.mockResolvedValueOnce({ accepted: true })

    await useJobStore.getState().batchRerun('ws1')

    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1'] }),
      })
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('handles 404 on batch rerun gracefully', async () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockApi.mockRejectedValueOnce(err)
    const warnSpy = vi
      .spyOn(console, 'warn')
      .mockImplementation(() => undefined)

    await useJobStore.getState().batchRerun('ws1')

    expect(warnSpy).toHaveBeenCalledWith(
      'Batch rerun endpoint is not implemented yet'
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    warnSpy.mockRestore()
  })

  it('calls batch delete endpoint and removes jobs on success', async () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          status: 'completed',
          source_id: 'Q1',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
        {
          id: 'j2',
          status: 'completed',
          source_id: 'Q2',
          title: '',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
        },
      ] as JobRecord[],
      selectedIds: new Set(['j1']),
    })
    mockApi.mockResolvedValueOnce({ deleted: 1 })

    await useJobStore.getState().batchDelete('ws1')

    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch',
      expect.objectContaining({
        method: 'DELETE',
        body: JSON.stringify({ job_ids: ['j1'] }),
      })
    )
    expect(useJobStore.getState().jobs).toHaveLength(1)
    expect(useJobStore.getState().jobs[0].id).toBe('j2')
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('handles 404 on batch delete gracefully', async () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockApi.mockRejectedValueOnce(err)
    const warnSpy = vi
      .spyOn(console, 'warn')
      .mockImplementation(() => undefined)

    await useJobStore.getState().batchDelete('ws1')

    expect(warnSpy).toHaveBeenCalledWith(
      'Batch delete endpoint is not implemented yet'
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    warnSpy.mockRestore()
  })

  it('does nothing when batch actions are invoked with empty selection', async () => {
    await useJobStore.getState().batchRerun('ws1')
    await useJobStore.getState().batchDelete('ws1')
    expect(mockApi).not.toHaveBeenCalled()
  })
})
