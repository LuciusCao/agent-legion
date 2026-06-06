import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
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
      <BrowserRouter>
        <DashboardPage />
      </BrowserRouter>
    )
    expect(screen.getByText('Agent Legion')).toBeInTheDocument()
  })

  it('renders Video Hive card', () => {
    render(
      <BrowserRouter>
        <DashboardPage />
      </BrowserRouter>
    )
    expect(screen.getByText('Video Hive')).toBeInTheDocument()
  })
})
