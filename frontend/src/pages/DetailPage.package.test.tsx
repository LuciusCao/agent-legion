import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { DetailPage } from './DetailPage'
import { useDetailStore } from '../stores/detailStore'
import { useArtifactStore } from '../stores/artifactStore'
import { useInteractionStore } from '../stores/interactionStore'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

import { api } from '../api'
const mockApi = vi.mocked(api)

describe('DetailPage package button', () => {
  beforeEach(() => {
    global.ResizeObserver = vi.fn().mockImplementation(function () {
      return { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() }
    })
    useDetailStore.setState({
      currentVideo: null,
      log: '',
      activeTab: 'subtitles',
      isLoading: false,
    })
    useArtifactStore.setState({
      artifacts: {
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      },
    })
    useInteractionStore.setState({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
    })
    useVideoStore.setState({
      videos: [],
      selectedType: 'knowledge',
      statusFilter: 'all',
      searchQuery: '',
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
    })
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
    })
    mockApi.mockReset()
  })

  it('is enabled for completed unpacked video (packed=0)', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
          packed: 0,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter initialEntries={['/videos/v1']}>
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Video 1')).toBeInTheDocument()
    })

    const btn = screen.getByTitle('打包') as HTMLElement
    console.log('disabled attr value:', btn.getAttribute('disabled'))
    console.log('hasAttribute disabled:', btn.hasAttribute('disabled'))
    console.log('btn outerHTML:', btn.outerHTML)
    console.log(
      'currentVideo from store:',
      JSON.stringify(useDetailStore.getState().currentVideo)
    )
    expect(btn.hasAttribute('disabled')).toBe(false)
  })
})
