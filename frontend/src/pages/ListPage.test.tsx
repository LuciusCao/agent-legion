import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { ListPage } from './ListPage'
import { useVideoStore } from '../stores/videoStore'
import { api } from '../api'

const mockApi = vi.fn()
vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
}))

vi.mock('../layouts/AppShell', () => ({
  useAppShellScroll: () => ({
    reportScrolled: vi.fn(),
    resetReportedScroll: vi.fn(),
  }),
}))

describe('ListPage', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useVideoStore.setState({
      videos: [],
      selectedType: 'knowledge',
      statusFilter: 'all',
      searchQuery: '',
      packedFilter: 'all',
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
      sseConnected: true,
      error: null,
    })
  })

  it('renders list page', async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/worker/status')
        return Promise.resolve({ paused: false })
      return Promise.resolve({ videos: [] })
    })
    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ListPage />
      </MemoryRouter>
    )
    await act(async () => {})
    expect(screen.getByText('知识点')).toBeInTheDocument()
    expect(screen.queryByTitle('刷新')).not.toBeInTheDocument()
    expect(screen.queryByTitle('打包')).not.toBeInTheDocument()
    expect(screen.getByTitle('多选')).toBeInTheDocument()
  })

  it('filters list by content type when tab changes', async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/worker/status')
        return Promise.resolve({ paused: false })
      return Promise.resolve({
        videos: [
          {
            id: 'v1',
            title: '知识视频A',
            content_type: 'knowledge',
            external_id: 'k1',
            status: 'completed',
            current_phase: 'package',
            error_message: '',
          },
          {
            id: 'v2',
            title: '题目视频B',
            content_type: 'question',
            external_id: 'q1',
            status: 'completed',
            current_phase: 'package',
            error_message: '',
          },
        ],
      })
    })
    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ListPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('知识视频A')).toBeInTheDocument()
    })
    expect(screen.queryByText('题目视频B')).not.toBeInTheDocument()

    const tabs = document.querySelector('md-tabs') as HTMLElement & {
      activeTabIndex: number
    }
    act(() => {
      tabs.activeTabIndex = 1
      tabs.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await waitFor(() => {
      expect(screen.queryByText('知识视频A')).not.toBeInTheDocument()
    })
    expect(screen.getByText('题目视频B')).toBeInTheDocument()
  })

  it('shows the persisted search query when returning to the list', async () => {
    useVideoStore.setState({ searchQuery: 'K001' })
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/worker/status')
        return Promise.resolve({ paused: false })
      return Promise.resolve({
        videos: [
          {
            id: 'v1',
            title: '知识视频A',
            content_type: 'knowledge',
            external_id: 'K001',
            status: 'completed',
            current_phase: 'package',
            error_message: '',
          },
          {
            id: 'v2',
            title: '知识视频B',
            content_type: 'knowledge',
            external_id: 'K002',
            status: 'completed',
            current_phase: 'package',
            error_message: '',
          },
        ],
      })
    })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ListPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('知识视频A')).toBeInTheDocument()
    })

    const search = document.querySelector('md-outlined-text-field')
    expect(search).toHaveAttribute('value', 'K001')
    expect(screen.queryByText('知识视频B')).not.toBeInTheDocument()
  })
})
