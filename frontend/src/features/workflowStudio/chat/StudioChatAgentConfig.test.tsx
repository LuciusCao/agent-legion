import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatAgentConfig } from './StudioChatAgentConfig'
import * as configApi from './studioChatConfigApi'
import type { StudioChatSessionRecord } from './studioChatApi'

vi.mock('./studioChatConfigApi')
const mockApi = vi.mocked(configApi)

const KIMI_MODES = {
  currentModeId: 'default',
  availableModes: [
    { id: 'default', name: 'Default' },
    { id: 'plan', name: 'Plan' },
    { id: 'yolo', name: 'Yolo' },
  ],
}
const KIMI_OPTIONS = [
  {
    id: 'model',
    name: 'Model',
    category: 'model',
    type: 'select',
    currentValue: 'k3',
    options: [
      {
        group: 'kimi',
        name: 'Kimi',
        options: [
          { value: 'k3', name: 'K3' },
          { value: 'k3-256k', name: 'K3 256k' },
        ],
      },
    ],
  },
  {
    id: 'thinking',
    name: 'Thinking',
    category: 'thought_level',
    type: 'select',
    currentValue: 'high',
    options: [{ value: 'low' }, { value: 'high' }, { value: 'max' }],
  },
  {
    id: 'sandbox',
    name: 'Sandbox',
    category: '_sandbox',
    type: 'select',
    currentValue: 'strict',
    options: [{ value: 'strict' }, { value: 'loose' }],
  },
]

function record(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: 'acp-1',
    capability_snapshot: { sessionModes: true, sessionConfigOptions: true },
    allow_all_permissions: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    session_modes: KIMI_MODES,
    config_options: KIMI_OPTIONS,
    ...overrides,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
})

/** 等切换的异步状态（pending → null）落地，避免 act() 外的更新警告。 */
const settled = (control: HTMLElement) =>
  waitFor(() => expect(control).toBeEnabled())

describe('StudioChatAgentConfig', () => {
  it('renders nothing for agents that advertise no config surface', () => {
    const { container } = render(
      <StudioChatAgentConfig
        workspaceId="ws1"
        session={record({
          capability_snapshot: {},
          session_modes: null,
          config_options: null,
        })}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('switches the agent mode through the API and shows the returned snapshot', async () => {
    mockApi.setStudioChatMode.mockResolvedValue(
      record({ session_modes: { ...KIMI_MODES, currentModeId: 'plan' } })
    )
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    const select = screen.getByLabelText('Agent 权限模式') as HTMLSelectElement
    expect(select.value).toBe('default')
    fireEvent.change(select, { target: { value: 'plan' } })
    expect(mockApi.setStudioChatMode).toHaveBeenCalledWith('ws1', 's1', 'plan')
    await waitFor(() => expect(select.value).toBe('plan'))
  })

  it('maps the generic thought level to the native value (kimi medium → low, labelled)', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    const select = screen.getByLabelText('思考档位') as HTMLSelectElement
    expect(select.value).toBe('high')
    expect(
      screen.getByRole('option', { name: 'medium（→ low）' })
    ).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'medium' } })
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'thinking',
      'low'
    )
    await settled(select)
  })

  it('renders grouped model options as optgroup and sends the nested value', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    expect(screen.getByRole('group', { name: 'Kimi' })).toBeInTheDocument()
    const select = screen.getByLabelText('模型')
    fireEvent.change(select, { target: { value: 'k3-256k' } })
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'model',
      'k3-256k'
    )
    await settled(select)
  })

  it('folds unknown / custom categories into 高级设置', () => {
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    expect(screen.getByText('高级设置')).toBeInTheDocument()
    expect(screen.getByLabelText('Sandbox')).toBeInTheDocument()
  })

  it('degrades a single-level thought list to a read-only control', () => {
    const single = [
      { ...KIMI_OPTIONS[1], currentValue: 'off', options: [{ value: 'off' }] },
    ]
    render(
      <StudioChatAgentConfig
        workspaceId="ws1"
        session={record({ config_options: single })}
      />
    )
    expect(screen.getByLabelText('思考档位')).toBeDisabled()
    expect(screen.getByText('（该模型不可调）')).toBeInTheDocument()
  })

  it('surfaces the server rejection and keeps the previous value', async () => {
    mockApi.setStudioChatMode.mockRejectedValue(
      new Error('Agent rejected the change: nope')
    )
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    const select = screen.getByLabelText('Agent 权限模式') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'yolo' } })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Agent rejected the change: nope'
      )
    )
    expect(select.value).toBe('default')
    await settled(select)
  })

  it('a newer session snapshot (SSE) wins over the local override', async () => {
    mockApi.setStudioChatMode.mockResolvedValue(
      record({ session_modes: { ...KIMI_MODES, currentModeId: 'plan' } })
    )
    const { rerender } = render(
      <StudioChatAgentConfig workspaceId="ws1" session={record()} />
    )
    const select = screen.getByLabelText('Agent 权限模式') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'plan' } })
    await waitFor(() => expect(select.value).toBe('plan'))
    // agent's own notification: the mirror now says yolo — newer than our response.
    rerender(
      <StudioChatAgentConfig
        workspaceId="ws1"
        session={record({
          session_modes: { ...KIMI_MODES, currentModeId: 'yolo' },
        })}
      />
    )
    expect(select.value).toBe('yolo')
  })

  it('announces thought-level drift after a model switch, not after own thought change', async () => {
    const shifted = record({
      config_options: [
        { ...KIMI_OPTIONS[0], currentValue: 'k3-256k' },
        { ...KIMI_OPTIONS[1], currentValue: 'low' },
      ],
    })
    mockApi.setStudioChatConfigOption.mockResolvedValue(shifted)
    render(<StudioChatAgentConfig workspaceId="ws1" session={record()} />)
    fireEvent.change(screen.getByLabelText('模型'), {
      target: { value: 'k3-256k' },
    })
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        '思考档位已随模型切换变为 low'
      )
    )
    await settled(screen.getByLabelText('模型'))
  })

  it('keeps unknown advertised thought values selectable and switchable back', async () => {
    const withTurbo = record({
      config_options: [
        {
          ...KIMI_OPTIONS[1],
          options: [...KIMI_OPTIONS[1].options, { value: 'turbo' }],
        },
      ],
    })
    mockApi.setStudioChatConfigOption.mockResolvedValue(withTurbo)
    render(<StudioChatAgentConfig workspaceId="ws1" session={withTurbo} />)
    const select = screen.getByLabelText('思考档位') as HTMLSelectElement
    // visible even while a known level is current
    expect(screen.getByRole('option', { name: 'turbo' })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'turbo' } })
    expect(mockApi.setStudioChatConfigOption).toHaveBeenLastCalledWith(
      'ws1',
      's1',
      'thinking',
      'turbo'
    )
    await settled(select)
    // and from an unknown current value the generic levels still switch back
    fireEvent.change(select, { target: { value: 'high' } })
    expect(mockApi.setStudioChatConfigOption).toHaveBeenLastCalledWith(
      'ws1',
      's1',
      'thinking',
      'high'
    )
    await settled(select)
  })

  it("does not leak a switched-away session's pending / error into the new session", async () => {
    let reject: (cause: Error) => void = () => undefined
    mockApi.setStudioChatMode.mockImplementation(
      () =>
        new Promise((_resolve, rej) => {
          reject = rej
        })
    )
    const { rerender } = render(
      <StudioChatAgentConfig workspaceId="ws1" session={record()} />
    )
    fireEvent.change(screen.getByLabelText('Agent 权限模式'), {
      target: { value: 'plan' },
    })
    expect(screen.getByLabelText('Agent 权限模式')).toBeDisabled()
    // the user switches to session B while A's request is still in flight
    rerender(
      <StudioChatAgentConfig workspaceId="ws1" session={record({ id: 's2' })} />
    )
    const selectB = screen.getByLabelText('Agent 权限模式')
    expect(selectB).toBeEnabled()
    reject(new Error('A rejected'))
    await waitFor(() => expect(mockApi.setStudioChatMode).toHaveBeenCalled())
    await settled(selectB)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
