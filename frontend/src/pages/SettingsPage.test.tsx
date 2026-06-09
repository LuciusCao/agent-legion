import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SettingsPage } from './SettingsPage'
import { useSettingStore } from '../stores/settingStore'
import { useUiStore } from '../stores/uiStore'
import { api } from '../api'

vi.mock('../api', () => ({ api: vi.fn() }))

const mockApi = vi.mocked(api)

const defaultState = {
  workspaceId: 'ws1',
  settings: {
    cmsUrl: '',
    cmsToken: '',
    entityType: 'question' as const,
    intakeModes: [],
    labelOverrides: {},
    pipelineKey: '',
    agentIds: [],
    concurrencyLimit: 1,
  },
  testStatus: { state: 'idle' as const },
  isSaving: false,
  saveError: null as string | null,
}

function renderPage(initialEntries = ['/workspaces/ws1/settings']) {
  return render(
    <MemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      initialEntries={initialEntries}
    >
      <Routes>
        <Route
          path="/workspaces/:workspaceId/settings"
          element={<SettingsPage />}
        />
        <Route
          path="/workspaces/:workspaceId"
          element={<div>Workspace main</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('SettingsPage', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    useUiStore.setState({ toast: null })
    mockApi.mockReset()
    mockApi.mockResolvedValue({})
  })

  it('renders all 4 cards', () => {
    renderPage()
    expect(screen.getByText('资源连接')).toBeInTheDocument()
    expect(screen.getByText('接入模式')).toBeInTheDocument()
    expect(screen.getByText('流水线')).toBeInTheDocument()
    expect(screen.getByText('智能体')).toBeInTheDocument()
  })

  it('navigates back to workspace main page', () => {
    renderPage()
    const back = screen.getByText('◀ 返回')
    fireEvent.click(back)
    expect(screen.getByText('Workspace main')).toBeInTheDocument()
  })

  it('calls fetchSettings on mount', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
  })

  it('calls test connection and shows status change', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
    mockApi.mockResolvedValueOnce({ ok: true, message: 'ok' })
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      const successBadge = document.querySelector('.status-badge.success')
      expect(successBadge).toBeInTheDocument()
      expect(successBadge?.textContent).toContain('连接成功')
    })
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/settings/test-connection',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('shows failed status and toast on test connection failure', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
    mockApi.mockRejectedValueOnce(new Error('connection refused'))
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      const failedBadge = document.querySelector('.status-badge.failed')
      expect(failedBadge).toBeInTheDocument()
      expect(failedBadge?.textContent).toContain('连接失败')
    })
    expect(useUiStore.getState().toast).toEqual({
      message: 'connection refused',
      type: 'error',
    })
  })

  it('calls saveSection when connection save is clicked', async () => {
    useSettingStore.setState({
      settings: {
        ...defaultState.settings,
        cmsUrl: 'https://cms.example.com',
      },
    })
    renderPage()
    const saveBtn = screen.getAllByText('保存')[0]
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws1/settings/connection',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({
            cmsUrl: 'https://cms.example.com',
            cmsToken: '',
          }),
        })
      )
    })
  })

  it('displays save error when saveSection fails', async () => {
    mockApi.mockRejectedValueOnce(
      Object.assign(new Error('Server Error'), { status: 500 })
    )
    useSettingStore.setState({
      settings: {
        ...defaultState.settings,
        cmsUrl: 'https://cms.example.com',
      },
    })
    renderPage()
    const saveBtn = screen.getAllByText('保存')[0]
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(screen.getByText('Server Error')).toBeInTheDocument()
    })
  })

  it('updates labelOverrides state when textarea input is valid JSON', () => {
    renderPage()
    const intakeHeader = screen.getByText('接入模式')
    fireEvent.click(intakeHeader)
    const textarea = document.querySelector(
      'md-outlined-text-field[label="标签覆盖 (JSON)"]'
    ) as HTMLElement
    expect(textarea).toBeTruthy()
    ;(textarea as HTMLInputElement).value = '{"direct_ids":"输入 ID"}'
    fireEvent.input(textarea)
    expect(useSettingStore.getState().settings.labelOverrides).toEqual({
      direct_ids: '输入 ID',
    })
  })
})
