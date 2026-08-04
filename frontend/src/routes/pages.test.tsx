import { Suspense, type ComponentType, type LazyExoticComponent } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import {
  DashboardPage,
  JobDetailPage,
  LoginPage,
  MonitoringPage,
  SettingsPage,
  SetupPage,
  TokenUsagePage,
  UsersAdminPage,
  WorkflowStudioPage,
  WorkspaceLayout,
  WorkspaceMainPage,
} from './pages'

vi.mock('../pages/LoginPage', () => ({ default: () => 'route:login' }))
vi.mock('../pages/SetupPage', () => ({ default: () => 'route:setup' }))
vi.mock('../pages/UsersAdminPage', () => ({ default: () => 'route:users' }))
vi.mock('../pages/JobDetailPage', () => ({ default: () => 'route:job-detail' }))
vi.mock('../pages/DashboardPage', () => ({
  DashboardPage: () => 'route:dashboard',
}))
vi.mock('../layouts/WorkspaceLayout', () => ({
  default: () => 'route:workspace-layout',
}))
vi.mock('../pages/SettingsPage', () => ({
  SettingsPage: () => 'route:settings',
}))
vi.mock('../pages/WorkflowStudioPage', () => ({
  WorkflowStudioPage: () => 'route:workflow-studio',
}))
vi.mock('../pages/WorkspaceMainPage', () => ({
  default: () => 'route:workspace-main',
}))
vi.mock('../pages/TokenUsagePage', () => ({
  TokenUsagePage: () => 'route:token-usage',
}))
vi.mock('../pages/MonitoringPage', () => ({
  default: () => 'route:monitoring',
}))

const routeCases: Array<[LazyExoticComponent<ComponentType>, string]> = [
  [LoginPage, 'route:login'],
  [SetupPage, 'route:setup'],
  [UsersAdminPage, 'route:users'],
  [JobDetailPage, 'route:job-detail'],
  [DashboardPage, 'route:dashboard'],
  [WorkspaceLayout, 'route:workspace-layout'],
  [SettingsPage, 'route:settings'],
  [WorkflowStudioPage, 'route:workflow-studio'],
  [WorkspaceMainPage, 'route:workspace-main'],
  [TokenUsagePage, 'route:token-usage'],
  [MonitoringPage, 'route:monitoring'],
]

describe('lazy route mapping', () => {
  it.each(routeCases)('loads the expected page module', async (Page, label) => {
    render(
      <MemoryRouter>
        <Suspense fallback={<div>loading route</div>}>
          <Page />
        </Suspense>
      </MemoryRouter>
    )

    expect(await screen.findByText(label)).toBeInTheDocument()
  })
})
