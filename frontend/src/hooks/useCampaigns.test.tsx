import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { useCampaigns } from './useCampaigns'
import { useCampaign } from './useCampaign'
import { createTestQueryClient } from '../testing/testQueryClient'
import { fetchCampaign, fetchCampaigns } from '../api/campaignApi'
import { makeCampaign } from '../components/campaign/testHelpers'

vi.mock('../api/campaignApi', () => ({
  fetchCampaigns: vi.fn(),
  fetchCampaign: vi.fn(),
}))

const mockFetchCampaigns = vi.mocked(fetchCampaigns)
const mockFetchCampaign = vi.mocked(fetchCampaign)

function makeWrapper() {
  const testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)
  return wrapper
}

describe('useCampaigns', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the newest-first list for the workspace', async () => {
    const campaigns = [makeCampaign()]
    mockFetchCampaigns.mockResolvedValue({ campaigns })
    const { result } = renderHook(() => useCampaigns('ws1'), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.data).toEqual({ campaigns })
    })
    expect(mockFetchCampaigns).toHaveBeenCalledWith('ws1')
  })

  it('stays disabled when workspaceId is undefined', async () => {
    const { result } = renderHook(() => useCampaigns(undefined), {
      wrapper: makeWrapper(),
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetchCampaigns).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })
})

describe('useCampaign', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the campaign detail by id', async () => {
    const campaign = makeCampaign()
    mockFetchCampaign.mockResolvedValue({ campaign })
    const { result } = renderHook(() => useCampaign('ws1', 'camp-0001'), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current.data).toEqual({ campaign })
    })
    expect(mockFetchCampaign).toHaveBeenCalledWith('ws1', 'camp-0001')
  })

  it('stays disabled when campaignId is null', async () => {
    const { result } = renderHook(() => useCampaign('ws1', null), {
      wrapper: makeWrapper(),
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockFetchCampaign).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })
})
