import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { VideoHiveSettingsPage } from './VideoHiveSettingsPage'

vi.mock('../api', () => ({
  api: vi.fn((path: string) => {
    if (path === '/api/global-services') {
      return Promise.resolve({
        cms: {
          baseUrl: 'https://example.com',
          tokenConfigured: true,
          env: 'prod',
        },
      })
    }
    if (path === '/api/video-hive/config') {
      return Promise.resolve({
        asr: {
          provider: 'auto',
          whisperConfigured: true,
          sensevoiceConfigured: false,
          vadEnabled: true,
        },
        openclaw: {
          runnerCount: 2,
          timeoutSeconds: 600,
        },
      })
    }
    return Promise.resolve({})
  }),
}))

describe('VideoHiveSettingsPage', () => {
  it('renders settings sections', async () => {
    render(
      <MemoryRouter>
        <VideoHiveSettingsPage />
      </MemoryRouter>
    )
    expect(screen.getByText('全局服务状态')).toBeInTheDocument()
    expect(screen.getByText('Worker 控制')).toBeInTheDocument()
    expect(screen.getByText('流水线信息')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('https://example.com')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByText('auto')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })
})
