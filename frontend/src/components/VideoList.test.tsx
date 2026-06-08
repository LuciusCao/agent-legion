import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { VideoList } from './VideoList'
import { useVideoStore } from '../stores/videoStore'

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
    const { container } = render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    expect(container.querySelectorAll('md-checkbox').length).toBe(0)
  })

  it('renders unchecked checkboxes in select mode when nothing is selected', () => {
    useVideoStore.setState({ selectMode: true })
    const { container } = render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    const checkboxes = container.querySelectorAll('md-checkbox')
    expect(checkboxes.length).toBe(2)
    checkboxes.forEach((cb) => {
      // checked="false" would still be interpreted as checked by Material Web
      expect(cb.hasAttribute('checked')).toBe(false)
    })
  })

  it('renders checked checkboxes only for selected videos', () => {
    useVideoStore.setState({
      selectMode: true,
      selectedIds: new Set(['v1']),
    })
    const { container } = render(
      <MemoryRouter>
        <VideoList />
      </MemoryRouter>
    )
    const checkboxes = Array.from(container.querySelectorAll('md-checkbox'))
    expect(checkboxes[0].hasAttribute('checked')).toBe(true)
    expect(checkboxes[1].hasAttribute('checked')).toBe(false)
  })
})
