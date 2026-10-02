import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchWorkerConsole } from '../api/agentWorkers'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { createTestQueryClient } from '../testing/testQueryClient'
import { useWorkerConsoleUrl } from './useWorkerConsoleUrl'

vi.mock('../api/agentWorkers', () => ({ fetchWorkerConsole: vi.fn() }))
const fetchConsole = vi.mocked(fetchWorkerConsole)
beforeEach(() => vi.resetAllMocks())

function setup(enabled = true) {
  const client = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return {
    client,
    ...renderHook(() => useWorkerConsoleUrl(enabled), { wrapper }),
  }
}

describe('deployment console metadata', () => {
  it.each(['http://localhost:8787', ''])(
    'preserves the successful snapshot %j on refresh failure',
    async (url) => {
      fetchConsole.mockResolvedValueOnce({ console_url: url })
      const { result, client } = setup()
      await waitFor(() => expect(result.current).toBe(url))
      fetchConsole.mockRejectedValueOnce(new Error('offline'))
      await act(() =>
        client.invalidateQueries({ queryKey: extraQueryKeys.workerConsole() })
      )
      expect(client.getQueryState(extraQueryKeys.workerConsole())?.status).toBe(
        'error'
      )
      expect(result.current).toBe(url)
      const replacement = url ? '' : 'http://replacement:8787'
      fetchConsole.mockResolvedValueOnce({ console_url: replacement })
      await act(() =>
        client.invalidateQueries({ queryKey: extraQueryKeys.workerConsole() })
      )
      await waitFor(() => expect(result.current).toBe(replacement))
    }
  )

  it('keeps an initial error unknown instead of claiming the address is unset', async () => {
    fetchConsole.mockRejectedValueOnce(new Error('offline'))
    const { result, client } = setup()
    await waitFor(() =>
      expect(client.getQueryState(extraQueryKeys.workerConsole())?.status).toBe(
        'error'
      )
    )
    expect(result.current).toBeUndefined()
  })

  it('does not fetch metadata for a disabled consumer', () => {
    setup(false)
    expect(fetchConsole).not.toHaveBeenCalled()
  })
})
