import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  createMemoryRouter,
  RouterProvider,
  type RouteObject,
} from 'react-router-dom'
import App from './App'

const { connectAgentsWs, disconnectAgentsWs } = vi.hoisted(() => ({
  connectAgentsWs: vi.fn(),
  disconnectAgentsWs: vi.fn(),
}))

vi.mock('./stores/agentsStore', () => ({
  useAgentsStore: () => ({ connectAgentsWs }),
}))

vi.mock('./AppRoutes', () => ({
  default: () => <div>application routes</div>,
}))

vi.mock('./components/Toast', () => ({
  default: () => <div>toast region</div>,
}))

beforeEach(() => {
  vi.clearAllMocks()
  connectAgentsWs.mockReturnValue(disconnectAgentsWs)
})

describe('App startup lifecycle', () => {
  it('connects realtime UI state and closes it on unmount', () => {
    const routes: RouteObject[] = [{ path: '*', element: <App /> }]
    const router = createMemoryRouter(routes, { initialEntries: ['/'] })
    const view = render(
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    )

    expect(connectAgentsWs).toHaveBeenCalledOnce()
    expect(screen.getByText('application routes')).toBeInTheDocument()
    expect(screen.getByText('toast region')).toBeInTheDocument()

    view.unmount()

    expect(disconnectAgentsWs).toHaveBeenCalledOnce()
  })
})
