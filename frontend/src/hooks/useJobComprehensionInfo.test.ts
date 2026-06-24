import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useJobComprehensionInfo } from './useJobComprehensionInfo'
import { fetchJobArtifact } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchJobArtifact: vi.fn(),
  }
})

const mockFetchJobArtifact = vi.mocked(fetchJobArtifact)

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
  })

  it('returns comprehension info from main artifact', async () => {
    mockFetchJobArtifact.mockResolvedValue(artifact(JSON.stringify(baseInfo)))

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

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

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

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

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

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

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
  })

  it('returns null when intermediate possible errors is not an array', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ key_info_list: [{ id: 'k1' }] }))
      )
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: 'bad' }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
  })

  it('returns null when intermediate artifacts exist but extraction fails', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(artifact(JSON.stringify({ key_info_list: [] })))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: [{ id: 'e1' }] }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('returns null when only one intermediate artifact is available', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockRejectedValueOnce(new Error('key info failed'))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ possible_error_list: [{ id: 'e1' }] }))
      )

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('returns null when possible errors artifact fails', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce(
        artifact(JSON.stringify({ key_info_list: [{ id: 'k1' }] }))
      )
      .mockRejectedValueOnce(new Error('possible errors failed'))

    const { result } = renderHook(() => useJobComprehensionInfo('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.info).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('refetches when refresh key changes', async () => {
    mockFetchJobArtifact.mockResolvedValue(artifact(JSON.stringify(baseInfo)))

    const { rerender } = renderHook(
      ({ refreshKey }) => useJobComprehensionInfo('job1', refreshKey),
      { initialProps: { refreshKey: 'a' } }
    )

    await waitFor(() => expect(mockFetchJobArtifact).toHaveBeenCalledTimes(1))

    rerender({ refreshKey: 'b' })

    await waitFor(() => expect(mockFetchJobArtifact).toHaveBeenCalledTimes(2))
  })
})
