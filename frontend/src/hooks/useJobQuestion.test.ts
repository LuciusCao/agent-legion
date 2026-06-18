import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useJobQuestion } from './useJobQuestion'

const mockFetchJobArtifact = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  }
})

describe('useJobQuestion', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
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

    const { result } = renderHook(() => useJobQuestion('job1'))

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

    const { result } = renderHook(() => useJobQuestion('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.question).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('sets error when artifact fetch fails', async () => {
    mockFetchJobArtifact.mockRejectedValue(new Error('not found'))

    const { result } = renderHook(() => useJobQuestion('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('not found')
    expect(result.current.question).toBeNull()
  })

  it('sets error when content is invalid json', async () => {
    mockFetchJobArtifact.mockResolvedValue({ content: 'not-json' })

    const { result } = renderHook(() => useJobQuestion('job1'))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toContain('JSON')
    expect(result.current.question).toBeNull()
  })
})
