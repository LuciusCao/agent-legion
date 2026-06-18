import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { DashboardPage } from './DashboardPage'

vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: () => ({
    workspaces: [],
    workspaceStats: {},
    fetchWorkspaces: vi.fn(),
    fetchWorkspaceStats: vi.fn(),
  }),
}))

vi.mock('../stores/videoStore', () => ({
  useVideoStore: () => ({
    videos: [],
    fetchVideos: vi.fn(),
  }),
}))

describe('DashboardPage', () => {
  it('renders Agent Legion title', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Agent Legion')).toBeInTheDocument()
  })

  it('renders Video Hive card', () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Video Hive')).toBeInTheDocument()
  })
})
