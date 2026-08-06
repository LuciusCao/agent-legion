import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { useJobQuestion } from './useJobQuestion'
import { createTestQueryClient } from '../testing/testQueryClient'
import { queryKeys } from '../lib/queryKeys'
import type { JobDetail } from '../types/jobTypes'

const mockFetchJobArtifact = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  }
})

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

describe('useJobQuestion', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
    queryClient = createTestQueryClient()
  })

  it('returns normalized question from questions.json', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        questions: [
          {
            question_id: 'Q1',
            normalized: {
              stem: '<p>What is 1+1?</p>',
              options: [
                { label: 'A', content: '1' },
                { label: 'B', content: '2' },
              ],
              answer: ['B'],
            },
          },
        ],
      }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('')
    expect(result.current.question).toEqual({
      stem: '<p>What is 1+1?</p>',
      options: [
        { label: 'A', content: '1' },
        { label: 'B', content: '2' },
      ],
      answer: ['B'],
    })
  })

  it('returns null when questions.json has no questions', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({ questions: [] }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('sets error when artifact fetch fails', async () => {
    mockFetchJobArtifact.mockRejectedValue(new Error('not found'))

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('not found')
    expect(result.current.question).toBeNull()
  })

  it('sets error when content is invalid json', async () => {
    mockFetchJobArtifact.mockResolvedValue({ content: 'not-json' })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toContain('JSON')
    expect(result.current.question).toBeNull()
  })

  it('returns null when questions field is not an array', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({ questions: 'bad' }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('returns null when questions array is empty', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({ questions: [] }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('returns null when first question is not an object', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({ questions: ['string'] }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('returns null when normalized field is missing', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({ questions: [{ question_id: 'Q1' }] }),
    })

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('refetches when the producer node version changes in the shared detail query', async () => {
    mockFetchJobArtifact
      .mockRejectedValueOnce(new Error('not found'))
      .mockResolvedValueOnce({
        content: JSON.stringify({
          questions: [
            {
              question_id: 'Q1',
              normalized: { stem: '<p>Generated later</p>' },
            },
          ],
        }),
      })

    const producerNode = {
      node_key: 'fetch_questions',
      outputs: ['questions.json'],
      status: 'running',
      started_at: '2026-06-18T09:00:00Z',
    }
    queryClient.setQueryData(
      queryKeys.jobDetail('job1'),
      makeDetail([producerNode])
    )

    const { result } = renderHook(() => useJobQuestion('job1'), { wrapper })

    await waitFor(() => expect(result.current.error).toBe('not found'))

    act(() => {
      queryClient.setQueryData(
        queryKeys.jobDetail('job1'),
        makeDetail([
          {
            ...producerNode,
            status: 'completed',
            finished_at: '2026-06-18T10:00:00Z',
          },
        ])
      )
    })

    await waitFor(() =>
      expect(result.current.question?.stem).toBe('<p>Generated later</p>')
    )
    expect(mockFetchJobArtifact).toHaveBeenCalledTimes(2)
  })
})
