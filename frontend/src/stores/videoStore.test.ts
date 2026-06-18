import { describe, it, expect, vi, beforeEach } from 'vitest'
import { waitFor } from '@testing-library/react'
import { useVideoStore } from './videoStore'
import { makeVideo } from '../testing/fixtures'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

import { api } from '../api'

const mockApi = vi.mocked(api)

describe('videoStore', () => {
  beforeEach(() => {
    useVideoStore.setState({
      videos: [],
      selectedType: 'knowledge',
      statusFilter: 'all',
      searchQuery: '',
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
      error: null,
      _filteredVideos: [],
      _counts: {
        all: 0,
        queued: 0,
        running: 0,
        failed: 0,
        completed: 0,
        packed: 0,
        unpacked: 0,
      },
    })
    mockApi.mockClear()
  })

  it('fetches videos', async () => {
    mockApi.mockResolvedValueOnce({
      videos: [makeVideo({ id: 'v1', title: 'Test' })],
    })
    await useVideoStore.getState().fetchVideos()
    expect(useVideoStore.getState().videos).toHaveLength(1)
    expect(useVideoStore.getState().videos[0].id).toBe('v1')
  })

  it('toggles select mode', () => {
    useVideoStore.getState().toggleSelectMode()
    expect(useVideoStore.getState().selectMode).toBe(true)
  })

  it('selects only unpacked completed videos visible under current filters', () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: 'v1',
          title: 'A',
          external_id: 'K001',
          knowledge_code: 'K001',
          status: 'completed',
          current_phase: 'package',
          packed: true,
        }),
        makeVideo({
          id: 'v2',
          title: 'B',
          external_id: 'K002',
          knowledge_code: 'K002',
          status: 'completed',
          current_phase: 'package',
          packed: false,
        }),
      ],
      statusFilter: 'failed',
    })
    useVideoStore.getState().selectUnpacked()
    // failed filter shows no completed videos, so nothing should be selected
    expect(useVideoStore.getState().selectedIds).toEqual(new Set())
  })

  it('selects only approved completed videos in the current view', () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: 'approved-visible',
          title: 'Visible Approved',
          external_id: 'K001',
          knowledge_code: 'K001',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            example_practice: { passed: 2, total: 2 },
            interaction_summary: { passed: 1, total: 1 },
          },
          interaction_review_status: 'all_passed',
        }),
        makeVideo({
          id: 'summary-video-type',
          title: 'Visible Video Summary',
          external_id: 'K002',
          knowledge_code: 'K002',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            example_practice: { passed: 1, total: 1 },
            video_summary: { passed: 1, total: 1 },
          },
          interaction_review_status: 'all_passed',
        }),
        makeVideo({
          id: 'rejected',
          title: 'Visible Rejected',
          external_id: 'K003',
          knowledge_code: 'K003',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            example_practice: { passed: 1, total: 2 },
            interaction_summary: { passed: 1, total: 1 },
          },
        }),
        makeVideo({
          id: 'summary-only',
          title: 'Visible Summary Only',
          external_id: 'K004',
          knowledge_code: 'K004',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            interaction_summary: { passed: 1, total: 1 },
          },
          interaction_review_status: 'all_passed',
        }),
        makeVideo({
          id: 'hidden-by-search',
          title: 'Hidden Approved',
          external_id: 'H001',
          knowledge_code: 'H001',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            example_practice: { passed: 1, total: 1 },
            interaction_summary: { passed: 1, total: 1 },
          },
        }),
        makeVideo({
          id: 'question-approved',
          title: 'Visible Approved',
          external_id: 'Q001',
          content_type: 'question',
          question_id: 'Q001',
          knowledge_code: '',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_stats: {
            example_practice: { passed: 1, total: 1 },
            interaction_summary: { passed: 1, total: 1 },
          },
        }),
      ],
      selectedType: 'knowledge',
      statusFilter: 'completed',
      searchQuery: 'Visible',
      packedFilter: 'all',
    })

    useVideoStore.getState().selectReviewApproved()

    expect(useVideoStore.getState().selectedIds).toEqual(
      new Set(['approved-visible', 'summary-video-type', 'summary-only'])
    )
  })

  it('selects review partial or failed completed knowledge videos in the current view', () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: 'approved',
          title: 'Visible Approved',
          external_id: 'K001',
          knowledge_code: 'K001',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_review_status: 'all_passed',
        }),
        makeVideo({
          id: 'partial',
          title: 'Visible Partial',
          external_id: 'K002',
          knowledge_code: 'K002',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_review_status: 'partial',
        }),
        makeVideo({
          id: 'failed',
          title: 'Visible Failed',
          external_id: 'K003',
          knowledge_code: 'K003',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_review_status: 'all_failed',
        }),
        makeVideo({
          id: 'missing-review-status',
          title: 'Visible Missing Review Status',
          external_id: 'K004',
          knowledge_code: 'K004',
          status: 'completed',
          current_phase: 'package',
          packed: false,
        }),
        makeVideo({
          id: 'question-partial',
          title: 'Visible Partial',
          external_id: 'Q001',
          content_type: 'question',
          question_id: 'Q001',
          knowledge_code: '',
          status: 'completed',
          current_phase: 'package',
          packed: false,
          interaction_review_status: 'partial',
        }),
      ],
      selectedType: 'knowledge',
      statusFilter: 'completed',
      searchQuery: 'Visible',
      packedFilter: 'all',
    })

    useVideoStore.getState().selectReviewNotPassed()

    expect(useVideoStore.getState().selectedIds).toEqual(
      new Set(['partial', 'failed', 'missing-review-status'])
    )
  })

  it('toggles video selection', () => {
    useVideoStore.getState().toggleVideoSelection('v1')
    expect(useVideoStore.getState().selectedIds.has('v1')).toBe(true)
    useVideoStore.getState().toggleVideoSelection('v1')
    expect(useVideoStore.getState().selectedIds.has('v1')).toBe(false)
  })

  it('selects all videos visible under grouped status filters', () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: 'missing',
          title: 'Missing URL',
          external_id: 'K001',
          knowledge_code: 'K001',
          status: 'missing_url',
          current_phase: 'waiting_for_url',
        }),
        makeVideo({
          id: 'queued',
          title: 'Queued',
          external_id: 'K002',
          knowledge_code: 'K002',
        }),
      ],
      selectedType: 'knowledge',
      statusFilter: 'failed',
    })

    useVideoStore.getState().selectAllVisible()

    expect(useVideoStore.getState().selectedIds).toEqual(new Set(['missing']))
  })

  it('posts run-to requests', async () => {
    mockApi.mockResolvedValueOnce({
      result: {
        video_id: 'v1',
        status: 'queued',
        phase: 'chapter_generate',
        message: 'queued',
      },
      video: null,
    })

    const response = await useVideoStore
      .getState()
      .runTo('v1', 'chapter_generate', 'transcribe')

    expect(mockApi).toHaveBeenCalledWith('/api/videos/v1/run-to', {
      method: 'POST',
      body: JSON.stringify({
        target_phase: 'chapter_generate',
        start_phase: 'transcribe',
      }),
    })
    expect(response.result.video_id).toBe('v1')
  })

  it('posts batch run-to requests', async () => {
    mockApi.mockResolvedValueOnce({
      results: [
        {
          video_id: 'v1',
          status: 'queued',
          phase: 'assemble',
          message: 'queued',
        },
      ],
    })

    const response = await useVideoStore
      .getState()
      .batchRunTo(['v1', 'v2'], 'assemble')

    expect(mockApi).toHaveBeenCalledWith('/api/videos/batch/run-to', {
      method: 'POST',
      body: JSON.stringify({
        video_ids: ['v1', 'v2'],
        target_phase: 'assemble',
        start_phase: null,
      }),
    })
    expect(response.results).toHaveLength(1)
  })

  it('sets error state when batchDelete fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('delete failed'))
    await expect(useVideoStore.getState().batchDelete(['v1'])).rejects.toThrow(
      'delete failed'
    )
    expect(useVideoStore.getState().error).toBe('delete failed')
  })

  it('sets error state when batchRerun fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('rerun failed'))
    await expect(
      useVideoStore.getState().batchRerun(['v1'], 'download')
    ).rejects.toThrow('rerun failed')
    expect(useVideoStore.getState().error).toBe('rerun failed')
  })

  it('sets error state when batchRunTo fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('run-to failed'))
    await expect(
      useVideoStore.getState().batchRunTo(['v1'], 'assemble')
    ).rejects.toThrow('run-to failed')
    expect(useVideoStore.getState().error).toBe('run-to failed')
  })

  it('sets error state when batchPackage fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('package failed'))
    await expect(useVideoStore.getState().batchPackage(['v1'])).rejects.toThrow(
      'package failed'
    )
    expect(useVideoStore.getState().error).toBe('package failed')
  })

  it('computes _filteredVideos and _counts after videos change', async () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: '1',
          title: 'Alpha',
          external_id: 'K001',
          knowledge_code: 'K001',
        }),
        makeVideo({
          id: '2',
          title: 'Beta',
          external_id: 'K002',
          knowledge_code: 'K002',
          status: 'completed',
          current_phase: 'assemble',
          packed: true,
        }),
      ],
    })
    await waitFor(() => {
      expect(useVideoStore.getState()._counts.all).toBe(2)
    })
    const state = useVideoStore.getState()
    expect(Array.isArray(state._filteredVideos)).toBe(true)
    expect(state._counts.all).toBe(2)
    expect(state._counts.completed).toBe(1)
    expect(state._counts.queued).toBe(1)
    expect(state._counts.packed).toBe(1)
    expect(state._counts.unpacked).toBe(0)
  })

  it('updates _filteredVideos when searchQuery changes', async () => {
    useVideoStore.setState({
      videos: [
        makeVideo({
          id: '1',
          title: 'Alpha',
          external_id: 'K001',
          knowledge_code: 'K001',
        }),
        makeVideo({
          id: '2',
          title: 'Beta',
          external_id: 'K002',
          knowledge_code: 'K002',
          status: 'completed',
          current_phase: 'assemble',
        }),
      ],
    })
    await waitFor(() => {
      expect(useVideoStore.getState()._filteredVideos).toHaveLength(2)
    })
    const state1 = useVideoStore.getState()
    expect(state1._filteredVideos.length).toBe(2)

    useVideoStore.getState().setSearchQuery('Alpha')
    await waitFor(() => {
      expect(useVideoStore.getState()._filteredVideos).toHaveLength(1)
    })
    const state2 = useVideoStore.getState()
    expect(state2._filteredVideos.length).toBe(1)
    expect(state2._filteredVideos[0].title).toBe('Alpha')
  })
})
