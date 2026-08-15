import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { useJobComprehensionInfo } from './useJobComprehensionInfo'
import { fetchJobArtifact } from '../api'
import { createTestQueryClient } from '../testing/testQueryClient'
import { queryKeys } from '../lib/queryKeys'
import type { JobDetail } from '../types/jobTypes'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchJobArtifact: vi.fn(),
  }
})

const mockFetchJobArtifact = vi.mocked(fetchJobArtifact)

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return createElement(QueryClientProvider, { client: queryClient }, children)
}

function makeDetail(nodes: Record<string, unknown>[]): JobDetail {
  return {
    job: { id: 'job1', status: 'running', updated_at: 't0' },
    nodes,
    runs: [],
    artifacts: [],
  } as unknown as JobDetail
}

function artifact(content: string) {
  return { content, name: 'test' }
}

const baseInfo = {
  question_id: 'q1',
  fingerprint: 'fp1',
  fingerprint_source: 'parsed',
  fingerprint_missing: false,
  comprehension_data: {
    key_info_list: [{ id: 'k1', text: 'key' }],
    possible_error_list: [{ id: 'e1', text: 'error' }],
  },
}

describe('useJobComprehensionInfo', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
    queryClient = createTestQueryClient()
  })

  it('returns comprehension info from main artifact', async () => {
    mockFetchJobArtifact.mockResolvedValue(artifact(JSON.stringify(baseInfo)))

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toEqual(baseInfo)
    expect(result.current.error).toBe('')
  })

  it('falls back to intermediate artifacts when main artifact lacks key info', async () => {
    mockFetchJobArtifact
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ comprehension_data: { key_info_list: [] } }))
      )
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({
            question_id: 'q1',
            key_info_list: [{ id: 'k1' }],
          })
        )
      )
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({
            question_id: 'q1',
            possible_error_list: [{ id: 'e1' }],
          })
        )
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info?.question_id).toBe('q1')
    expect(result.current.info?.comprehension_data.key_info_list).toHaveLength(
      1
    )
  })

  it('returns null when main artifact is invalid and intermediate artifacts are missing', async () => {
    mockFetchJobArtifact
      .mockResolvedValueOnce(artifact(JSON.stringify({ invalid: true })))
      .mockResolvedValueOnce(
        null as unknown as Awaited<ReturnType<typeof fetchJobArtifact>>
      )
      .mockResolvedValueOnce(
        null as unknown as Awaited<ReturnType<typeof fetchJobArtifact>>
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
  })

  it('returns null when intermediate key info list is empty', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(artifact(JSON.stringify({ key_info_list: [] })))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: [] }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
  })

  it('returns key info only when possible errors intermediate is malformed', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ key_info_list: [{ id: 'k1' }] }))
      )
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: 'bad' }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).not.toBeNull()
    expect(result.current.info?.comprehension_data.key_info_list).toHaveLength(
      1
    )
    expect(
      result.current.info?.comprehension_data.possible_error_list
    ).toHaveLength(0)
  })

  it('returns partial info when only key info intermediate artifact is available', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({ question_id: 'q1', key_info_list: [{ id: 'k1' }] })
        )
      )
      .mockRejectedValueOnce(new Error('possible errors reviewed failed'))
      .mockRejectedValueOnce(new Error('possible errors raw failed'))

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).not.toBeNull()
    expect(result.current.info?.comprehension_data.key_info_list).toHaveLength(
      1
    )
    expect(
      result.current.info?.comprehension_data.possible_error_list
    ).toHaveLength(0)
    expect(result.current.info?.question_id).toBe('q1')
    expect(result.current.error).toBe('')
  })

  it('returns partial info when only possible errors intermediate artifact is available', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockRejectedValueOnce(new Error('key info reviewed failed'))
      .mockRejectedValueOnce(new Error('key info raw failed'))
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({
            question_id: 'q1',
            possible_error_list: [{ id: 'e1' }],
          })
        )
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).not.toBeNull()
    expect(result.current.info?.comprehension_data.key_info_list).toHaveLength(
      0
    )
    expect(
      result.current.info?.comprehension_data.possible_error_list
    ).toHaveLength(1)
    expect(result.current.info?.question_id).toBe('q1')
    expect(result.current.error).toBe('')
  })

  it('falls back to raw artifacts when reviewed artifacts are missing', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockRejectedValueOnce(new Error('reviewed key info not found'))
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({ question_id: 'q1', key_info_list: [{ id: 'k1' }] })
        )
      )
      .mockRejectedValueOnce(new Error('reviewed possible errors not found'))
      .mockResolvedValueOnce(
        artifact(
          JSON.stringify({
            question_id: 'q1',
            possible_error_list: [{ id: 'e1' }],
          })
        )
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).not.toBeNull()
    expect(result.current.info?.comprehension_data.key_info_list).toHaveLength(
      1
    )
    expect(
      result.current.info?.comprehension_data.possible_error_list
    ).toHaveLength(1)
    expect(result.current.info?.question_id).toBe('q1')
    expect(result.current.error).toBe('')
  })

  it('returns null when both intermediate artifacts are empty', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(artifact(JSON.stringify({ key_info_list: [] })))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: [] }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'), {
      wrapper,
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('refetches when the comprehension version changes in the shared detail query', async () => {
    mockFetchJobArtifact.mockResolvedValue(artifact(JSON.stringify(baseInfo)))

    const assembleNode = {
      node_key: 'assemble_items',
      status: 'running',
      started_at: '2026-06-18T09:00:00Z',
    }
    queryClient.setQueryData(
      queryKeys.jobDetail('job1'),
      makeDetail([assembleNode])
    )

    renderHook(() => useJobComprehensionInfo('job1'), { wrapper })

    await waitFor(() => expect(mockFetchJobArtifact).toHaveBeenCalledTimes(1))

    act(() => {
      queryClient.setQueryData(
        queryKeys.jobDetail('job1'),
        makeDetail([
          {
            ...assembleNode,
            status: 'completed',
            finished_at: '2026-06-18T10:00:00Z',
          },
        ])
      )
    })

    await waitFor(() => expect(mockFetchJobArtifact).toHaveBeenCalledTimes(2))
  })
})
