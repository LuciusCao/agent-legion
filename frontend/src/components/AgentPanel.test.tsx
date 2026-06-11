import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { AgentPanel } from './AgentPanel'
import { useUiStore } from '../stores/uiStore'

const mockApi = vi.fn()
vi.mock('../api', () => ({
  api: (...args: any[]) => mockApi(...args),
}))

describe('AgentPanel', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
      workerPaused: false,
    })
  })

  it('renders empty state when no agents', () => {
    render(<AgentPanel />)
    expect(screen.getByText(/暂无运行中的 Agent/)).toBeInTheDocument()
  })

  it('omits the summary title to keep the panel compact', () => {
    useUiStore.setState({
      agents: [
        {
          id: 'agent-1',
          name: 'Agent A',
          workspace_id: '',
          busy: false,
          task_count: 0,
          max_tasks: 1,
          current_video_id: null,
        },
      ],
    })

    render(<AgentPanel />)

    expect(screen.getByText('Agent A')).toBeInTheDocument()
    expect(screen.queryByText(/Agent 状态/)).not.toBeInTheDocument()
  })

  it('loads worker status and toggles queue scheduling', async () => {
    mockApi
      .mockResolvedValueOnce({ paused: false })
      .mockResolvedValueOnce({ paused: true })

    const { container } = render(<AgentPanel />)

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/worker/status')
    })
    expect(screen.getByText('自动调度开启')).toBeInTheDocument()

    const switchEl = container.querySelector('md-switch') as HTMLElement & {
      selected?: boolean
    }
    expect(switchEl).toBeInTheDocument()
    expect(switchEl).toHaveAttribute('selected', 'true')

    await act(async () => {
      switchEl.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(mockApi).toHaveBeenCalledWith('/api/worker/pause', {
      method: 'POST',
    })
    expect(useUiStore.getState().workerPaused).toBe(true)
    expect(screen.getByText('自动调度关闭')).toBeInTheDocument()
    expect(switchEl).not.toHaveAttribute('selected')
  })
})
