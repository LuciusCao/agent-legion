import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { StudioAgentsSection } from './StudioAgentsSection'
import {
  getStudioAgents,
  redetectStudioAgents,
  updateStudioAgents,
} from '../../api/studioAgents'
import type { StudioAgentRegistryResponse } from '../../api/studioAgents'

vi.mock('../../api/studioAgents', () => ({
  getStudioAgents: vi.fn(),
  updateStudioAgents: vi.fn(),
  redetectStudioAgents: vi.fn(),
}))

const registry: StudioAgentRegistryResponse = {
  api_base: 'http://127.0.0.1:8000',
  agents: [
    {
      id: 'kimi',
      label: 'Kimi Code',
      command: 'kimi',
      args: ['acp'],
      source: 'manual',
    },
    {
      id: 'claude',
      label: 'Claude Code',
      command: 'claude',
      args: [],
      source: 'manual',
    },
  ],
  availability: { kimi: true, claude: false },
  detection: {
    kimi: {
      detected: true,
      path: '/usr/local/bin/kimi',
      version: 'kimi 0.55.0',
    },
    claude: { detected: false, path: null, version: null },
  },
  // #355：GET/PUT 契约携带的内容版本（快照版本，随保存结果前进）。
  revision: 'rev-1',
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
    expect(screen.getByLabelText('平台回调地址（api_base）')).toHaveValue(
      'http://127.0.0.1:8000'
    )
    expect(screen.getByText('可用')).toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
    // 初始未编辑，保存按钮不可用
    expect(screen.getByText('保存')).toBeDisabled()
  })

  it('renders source and detection status per row', async () => {
    renderSection()
    await screen.findByLabelText('agent-id-0')

    // kimi：目录内且探测到 → 手工（默认）· 已检测到（版本）
    expect(
      screen.getByText(/手工 · 已检测到（kimi 0.55.0）/)
    ).toBeInTheDocument()
    // claude：目录内但未探测到
    expect(screen.getByText('未检测到')).toBeInTheDocument()
  })

  it('marks detected entries with the 自动检测 badge', async () => {
    vi.mocked(getStudioAgents).mockResolvedValue({
      ...registry,
      agents: [
        {
          id: 'kimi',
          label: 'Kimi Code',
          command: 'kimi',
          args: ['acp'],
          source: 'detected',
        },
      ],
    })
    renderSection()
    await screen.findByLabelText('agent-id-0')

    expect(screen.getByText(/自动检测 · 已检测到/)).toBeInTheDocument()
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
    fireEvent.change(screen.getByLabelText('平台回调地址（api_base）'), {
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
            source: 'manual',
          },
          {
            id: 'claude',
            label: 'Claude Code',
            command: 'claude',
            args: [],
            source: 'manual',
          },
        ],
        // #355：PUT 携带快照版本，服务端在写入事务内比对。
        revision: 'rev-1',
      })
    })
    // 保存成功后回到 clean 状态
    await waitFor(() => {
      expect(screen.getByText('保存')).toBeDisabled()
    })
  })

  it('redetects and refreshes rows from the server result', async () => {
    vi.mocked(redetectStudioAgents).mockResolvedValue({
      ...registry,
      agents: [
        ...(registry.agents ?? []),
        {
          id: 'goose',
          label: 'Goose',
          command: 'goose',
          args: ['acp'],
          source: 'detected',
        },
      ],
      detection: {
        ...registry.detection,
        goose: { detected: false, path: null, version: null },
      },
    })

    renderSection()
    await screen.findByLabelText('agent-id-0')

    fireEvent.click(screen.getByText('重新检测'))

    await waitFor(() => {
      expect(redetectStudioAgents).toHaveBeenCalledTimes(1)
    })
    // 新检测到的 agent 进入编辑行
    expect(await screen.findByLabelText('agent-id-2')).toHaveValue('goose')
    expect(screen.getByTestId('studio-agent-row-2')).toHaveTextContent(
      '自动检测 · 未检测到'
    )
  })

  it('disables redetect while there are unsaved edits', async () => {
    renderSection()
    await screen.findByLabelText('agent-id-0')

    expect(screen.getByText('重新检测')).toBeEnabled()
    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    expect(screen.getByText('重新检测')).toBeDisabled()
    fireEvent.click(screen.getByText('重新检测'))
    expect(redetectStudioAgents).not.toHaveBeenCalled()
  })

  it('shows the server error when redetect fails', async () => {
    vi.mocked(redetectStudioAgents).mockRejectedValue(new Error('HTTP 500'))
    renderSection()
    await screen.findByLabelText('agent-id-0')

    fireEvent.click(screen.getByText('重新检测'))

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500')
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
    await screen.findByLabelText('agent-label-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'HTTP 422: duplicate agent id'
    )
  })

  it('shows a refresh confirmation dialog on 409 and does not auto-retry', async () => {
    // #355：快照版本陈旧（保存间隙有探测合并等新写入）→ 后端 409。
    // 前端弹确认对话框提示刷新，不自动重试、不静默覆盖。
    vi.mocked(updateStudioAgents).mockRejectedValue(
      Object.assign(new Error('HTTP 409: registry revision mismatch'), {
        status: 409,
      })
    )

    renderSection()
    await screen.findByLabelText('agent-label-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('注册表已被其他修改更新')
    expect(dialog).toHaveTextContent('请刷新后基于最新内容重试')
    // 不自动重试：确认对话框出现后 PUT 仍只调用过一次。
    expect(updateStudioAgents).toHaveBeenCalledTimes(1)
    // 普通错误条不出现（409 走对话框而非 alert）。
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    // 「刷新注册表」使缓存失效重取（由管理员主动选择刷新）。
    fireEvent.click(screen.getByRole('button', { name: '刷新注册表' }))
    await waitFor(() => {
      expect(getStudioAgents).toHaveBeenCalledTimes(2)
    })
    // 「继续编辑」分支：再次 409 后关闭对话框，PUT 调用数不增加。
    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI 2' },
    })
    fireEvent.click(screen.getByText('保存'))
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(updateStudioAgents).toHaveBeenCalledTimes(2)
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getStudioAgents).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
