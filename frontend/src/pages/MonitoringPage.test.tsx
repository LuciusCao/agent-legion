import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { MonitoringPage } from './MonitoringPage'

vi.mock('../api/metrics', () => ({
  fetchOpsMetrics: vi.fn().mockResolvedValue({
    granularity: '6h',
    buckets: [],
  }),
}))

function renderPage() {
  return render(
    <MemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      initialEntries={['/monitoring']}
    >
      <Routes>
        <Route path="/monitoring" element={<MonitoringPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('MonitoringPage', () => {
  it('renders page title and back button', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('监控')).toBeInTheDocument()
    })
    expect(screen.getByTestId('app-bar-back')).toBeInTheDocument()
  })

  it('renders the monitoring panel', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('运维监控')).toBeInTheDocument()
    })
  })
})
