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

  it('shows a refresh dialog on 409; refresh adopts the 409 body so the next save succeeds', async () => {
    // #355 审核 P1：刷新必须真正前进编辑器（rows/baseline/revision），
    // 否则旧实现 invalidate 重取的数据不被 useState 编辑器消费，下一
    // 次保存仍持旧 revision——409 死循环。409 响应体携带服务端最新
    // 文档（探测合并进了 codex 行、revision 前进到 rev-2）。
    const concurrent: StudioAgentRegistryResponse = {
      ...registry,
      // codex P2：并发修改同时改了 api_base——刷新必须一并前进（否则
      // 立即 dirty、下次保存把旧地址写回覆盖并发修改）。
      api_base: 'http://127.0.0.1:9000',
      agents: [
        ...(registry.agents ?? []),
        {
          id: 'codex',
          label: 'Codex',
          command: 'codex',
          args: [],
          source: 'detected',
        },
      ],
      revision: 'rev-2',
    }
    vi.mocked(updateStudioAgents)
      .mockRejectedValueOnce(
        Object.assign(new Error('HTTP 409: registry revision mismatch'), {
          status: 409,
          body: concurrent,
        })
      )
      .mockResolvedValueOnce(concurrent)

    renderSection()
    await screen.findByLabelText('agent-label-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('注册表已被其他修改更新')
    // 不自动重试：确认对话框出现后 PUT 仍只调用过一次。
    expect(updateStudioAgents).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    // 「刷新注册表」直接采用 409 携带的最新文档——编辑器真正前进
    //（并发文档多了一行 codex，第三行 label 即其名）。
    fireEvent.click(screen.getByRole('button', { name: '刷新注册表' }))
    await waitFor(() => {
      expect(screen.getByLabelText('agent-label-2')).toHaveValue('Codex')
    })
    // api_base 同步前进（codex P2：不是只剩 rows 前进的半更新状态）。
    expect(screen.getByLabelText('平台回调地址（api_base）')).toHaveValue(
      'http://127.0.0.1:9000'
    )
    // 本地未保存的编辑被丢弃（对话框文案明示）。
    expect(screen.getByLabelText('agent-label-0')).toHaveValue('Kimi Code')

    // 关键断言（审核 P1 的回归钉）：再次保存携带刷新后的 rev-2，成功。
    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => {
      expect(updateStudioAgents).toHaveBeenCalledTimes(2)
    })
    expect(updateStudioAgents).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 'rev-2' })
    )
    // 第二次保存成功：对话框不再出现（MUI 关闭有过渡，waitFor 收敛）。
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('degrades to the error alert when the 409 body is not a registry', async () => {
    // 审核 P1 边界：异常路径（代理剥离响应体等）不能卡在无提示态。
    vi.mocked(updateStudioAgents).mockRejectedValue(
      Object.assign(new Error('HTTP 409: registry revision mismatch'), {
        status: 409,
        body: undefined,
      })
    )

    renderSection()
    await screen.findByLabelText('agent-label-0')

    fireEvent.change(screen.getByLabelText('agent-label-0'), {
      target: { value: 'Kimi CLI' },
    })
    fireEvent.click(screen.getByText('保存'))

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 409')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getStudioAgents).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
