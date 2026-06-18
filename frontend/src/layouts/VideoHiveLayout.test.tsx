import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import VideoHiveLayout from './VideoHiveLayout'
import { createMockUiState } from '../testing/fixtures'

const fetchWorkerStatusMock = vi.fn()

vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState({
      fetchWorkerStatus: fetchWorkerStatusMock,
      agents: [
        {
          id: 'main',
          name: 'Main',
          workspace_id: '',
          busy: false,
          task_count: 0,
          max_tasks: 8,
          current_video_id: null,
        },
      ],
    })
    return selector ? selector(state) : state
  },
}))

describe('VideoHiveLayout', () => {
  beforeEach(() => {
    fetchWorkerStatusMock.mockClear()
    fetchWorkerStatusMock.mockResolvedValue(undefined)
  })

  it('keeps the Video Hive agent status entry in the app bar', () => {
    render(
      <MemoryRouter initialEntries={['/video-hive']}>
        <Routes>
          <Route path="/video-hive" element={<VideoHiveLayout />} />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByLabelText('Agent 状态')).toBeInTheDocument()
  })
})
