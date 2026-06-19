import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { VideoList } from './VideoList'
import { useVideoStore } from '../stores/videoStore'

vi.mock('../layouts/AppShell', () => ({
  useAppShellScroll: () => ({
    reportScrolled: vi.fn(),
    resetReportedScroll: vi.fn(),
  }),
}))

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: vi.fn(() => ({
    getVirtualItems: () => [
      { key: 0, index: 0, start: 0, size: 72 },
      { key: 1, index: 1, start: 72, size: 72 },
    ],
    getTotalSize: () => 144,
  })),
}))

const mockVideos = [
  {
    id: 'v1',
    title: 'Video 1',
    source_url: '',
    content_type: 'knowledge' as const,
    external_id: 'K001',
    knowledge_code: 'K001',
    question_id: '',
    source_uuid: '',
    status: 'completed',
    current_phase: 'package',
    error_message: '',
    storage_dir: '',
    duration: 0,
    packed: false,
  },
  {
    id: 'v2',
    title: 'Video 2',
    source_url: '',
    content_type: 'knowledge' as const,
    external_id: 'K002',
    knowledge_code: 'K002',
    question_id: '',
    source_uuid: '',
    status: 'completed',
    current_phase: 'package',
    error_message: '',
    storage_dir: '',
    duration: 0,
    packed: false,
  },
]

describe('VideoList', () => {
  beforeEach(() => {
    useVideoStore.setState({
      videos: mockVideos,
      selectedType: 'knowledge',
      statusFilter: 'all',
      searchQuery: '',
      packedFilter: 'all',
      selectMode: false,
      selectedIds: new Set(),
    })
  })

  it('does not render checkboxes when not in select mode', () => {
    render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    expect(screen.queryAllByRole('checkbox').length).toBe(0)
  })

  it('renders unchecked checkboxes in select mode when nothing is selected', () => {
    useVideoStore.setState({ selectMode: true })
    render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBe(2)
    checkboxes.forEach((cb) => {
      expect((cb as HTMLInputElement).checked).toBe(false)
    })
  })

  it('renders checked checkboxes only for selected videos', () => {
    useVideoStore.setState({
      selectMode: true,
      selectedIds: new Set(['v1']),
    })
    render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    const checkboxes = screen.getAllByRole('checkbox')
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(true)
    expect((checkboxes[1] as HTMLInputElement).checked).toBe(false)
  })
})
