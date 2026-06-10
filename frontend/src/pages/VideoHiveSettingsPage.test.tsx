import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { VideoHiveSettingsPage } from './VideoHiveSettingsPage'

vi.mock('../api', () => ({
  api: vi.fn(() =>
    Promise.resolve({
      cms: {
        baseUrl: 'https://example.com',
        tokenConfigured: true,
        env: 'prod',
      },
    })
  ),
}))

describe('VideoHiveSettingsPage', () => {
  it('renders settings title', () => {
    render(
      <MemoryRouter>
        <VideoHiveSettingsPage />
      </MemoryRouter>
    )
    expect(screen.getByText('全局服务状态')).toBeInTheDocument()
    expect(screen.getByText('Worker 控制')).toBeInTheDocument()
  })
})
