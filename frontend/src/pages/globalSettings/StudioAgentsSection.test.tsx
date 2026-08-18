import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { StudioAgentsSection } from './StudioAgentsSection'
import { getStudioAgents, updateStudioAgents } from '../../api/studioAgents'
import type { StudioAgentRegistryResponse } from '../../api/studioAgents'

vi.mock('../../api/studioAgents', () => ({
  getStudioAgents: vi.fn(),
  updateStudioAgents: vi.fn(),
}))

const registry: StudioAgentRegistryResponse = {
  api_base: 'http://127.0.0.1:8000',
  agents: [
    { id: 'kimi', label: 'Kimi Code', command: 'kimi', args: ['acp'] },
    { id: 'claude', label: 'Claude Code', command: 'claude', args: [] },
  ],
  availability: { kimi: true, claude: false },
}

function renderSection() {
  return render(
    <MemoryRouter>
      <StudioAgentsSection />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getStudioAgents).mockResolvedValue(registry)
})

describe('StudioAgentsSection', () => {
  it('renders the registry rows with availability badges', async () => {
    renderSection()

    expect(await screen.findByLabelText('agent-id-0')).toHaveValue('kimi')
    expect(screen.getByLabelText('agent-label-0')).toHaveValue('Kimi Code')
    expect(screen.getByLabelText('agent-command-0')).toHaveValue('kimi')
    expect(screen.getByLabelText('agent-args-0')).toHaveValue('acp')
    expect(screen.getByLabelText('agent-id-1')).toHaveValue('claude')
    expect(screen.getByLabelText('api_base')).toHaveValue(
      'http://127.0.0.1:8000'
    )
    expect(screen.getByText('可用')).toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
    // 初始未编辑，保存按钮不可用
    expect(screen.getByText('保存')).toBeDisabled()
  })

  it('edits a row and saves the whole document via PUT', async () => {
    vi.mocked(updateStudioAgents).mockImplementation(async (payload) => ({
      ...payload,
      availability: { kimi: true, claude: false },
    }))

    renderSection()
    await screen.findByLabelText('agent-label-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.change(screen.getByLabelText('agent-args-0'), {
      target: { value: 'acp --verbose' },
    })
    fireEvent.change(screen.getByLabelText('api_base'), {
      target: { value: 'http://127.0.0.1:9000' },
    })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => {
      expect(updateStudioAgents).toHaveBeenCalledWith({
        api_base: 'http://127.0.0.1:9000',
        agents: [
          {
            id: 'kimi',
            label: 'Kimi CLI',
            command: 'kimi',
            args: ['acp', '--verbose'],
          },
          { id: 'claude', label: 'Claude Code', command: 'claude', args: [] },
        ],
      })
    })
    // 保存成功后回到 clean 状态
    await waitFor(() => {
      expect(screen.getByText('保存')).toBeDisabled()
    })
  })

  it('adds and deletes rows', async () => {
    renderSection()
    await screen.findByLabelText('agent-id-0')

    fireEvent.click(screen.getByText('添加 agent'))
    expect(screen.getByTestId('studio-agent-row-2')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('删除 agent 1'))
    expect(screen.queryByDisplayValue('claude')).not.toBeInTheDocument()
    // 新增行仍在（删除后重排为 index 1）
    expect(screen.getByTestId('studio-agent-row-1')).toBeInTheDocument()
    expect(screen.queryByTestId('studio-agent-row-2')).not.toBeInTheDocument()
  })

  it('rejects invalid id, blank fields and duplicate ids before saving', async () => {
    renderSection()
    await screen.findByLabelText('agent-id-0')

    fireEvent.change(screen.getByLabelText('agent-id-0'), {
      target: { value: 'BAD ID' },
    })
    fireEvent.click(screen.getByText('保存'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/id 不合法/)
    expect(updateStudioAgents).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('agent-id-0'), {
      target: { value: 'claude' },
    })
    fireEvent.click(screen.getByText('保存'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'agent id 重复：claude'
    )
    expect(updateStudioAgents).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('agent-id-0'), {
      target: { value: 'kimi' },
    })
    fireEvent.change(screen.getByLabelText('agent-command-0'), {
      target: { value: ' ' },
    })
    fireEvent.click(screen.getByText('保存'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /command 不能为空/
    )
    expect(updateStudioAgents).not.toHaveBeenCalled()
  })

  it('shows the server error when save fails', async () => {
    vi.mocked(updateStudioAgents).mockRejectedValue(
      new Error('HTTP 422: duplicate agent id')
    )

    renderSection()
    await screen.findByLabelText('agent-id-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'HTTP 422: duplicate agent id'
    )
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getStudioAgents).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
