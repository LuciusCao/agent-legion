import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatComposer } from './StudioChatComposer'
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
    compacting: false,
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

function renderComposer(
  overrides?: Partial<Parameters<typeof StudioChatComposer>[0]>
) {
  const onSend = vi.fn()
  render(
    <StudioChatComposer
      busy={false}
      disabled={false}
      disabledReason={null}
      onSend={onSend}
      {...overrides}
    />
  )
  return onSend
}

function renderWithConfig(session: StudioChatSessionRecord = record()) {
  return renderComposer({ config: { workspaceId: 'ws1', session } })
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('StudioChatComposer input', () => {
  it('does not send on Enter while an IME composition is active', () => {
    const onSend = renderComposer()
    const input = screen.getByLabelText('消息输入')
    fireEvent.change(input, { target: { value: '你好' } })
    // 中文输入法组合中的回车是确认候选，不能当成发送。
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
    // 组合结束后的回车正常发送。
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false })
    expect(onSend).toHaveBeenCalledWith('你好')
  })

  it('keeps the input enabled while busy and labels the button 排队', () => {
    renderComposer({ busy: true })
    expect(screen.getByLabelText('消息输入')).toBeEnabled()
    expect(screen.getByRole('button', { name: '排队' })).toBeInTheDocument()
    // #695 R3/R4：快捷键提示并入 placeholder，不再独占一行。
    expect(screen.getByLabelText('消息输入')).toHaveAttribute(
      'placeholder',
      expect.stringContaining('运行中发送将进入队列')
    )
  })

  it('shows no config chips without the config prop (diagnosis/preview)', () => {
    renderComposer()
    expect(
      screen.queryByRole('button', { name: 'Agent 权限模式' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })
})

describe('StudioChatComposer config chips (#695 R4)', () => {
  it('renders nothing for agents that advertise no config surface', () => {
    renderWithConfig(
      record({
        capability_snapshot: {},
        session_modes: null,
        config_options: null,
      })
    )
    expect(
      screen.queryByRole('button', { name: 'Agent 权限模式' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('switches the agent mode through the chip menu', async () => {
    mockApi.setStudioChatMode.mockResolvedValue(
      record({ session_modes: { ...KIMI_MODES, currentModeId: 'plan' } })
    )
    renderWithConfig()
    fireEvent.click(screen.getByRole('button', { name: 'Agent 权限模式' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Plan' }))
    expect(mockApi.setStudioChatMode).toHaveBeenCalledWith('ws1', 's1', 'plan')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Agent 权限模式' })
      ).toHaveTextContent('Plan')
    )
  })

  it('exposes the two-layer permission note on the mode chip (#658)', () => {
    renderWithConfig()
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toHaveAttribute('title', expect.stringContaining('两层互不改写'))
  })

  it('maps the generic thought level to the native value via the chip menu', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig()
    const chip = screen.getByRole('button', { name: '思考档位' })
    expect(chip).toHaveTextContent('思考 high')
    fireEvent.click(chip)
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'medium（→ low）' })
    )
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'thinking',
      'low'
    )
    // 等切换的异步状态（pending → null）落地，避免 act() 外的更新警告。
    await act(async () => {})
  })

  it('sends the native off value through the 关闭 menu item', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig(
      record({
        config_options: [
          {
            ...KIMI_OPTIONS[1],
            options: [{ value: 'none' }, ...KIMI_OPTIONS[1].options],
          },
        ],
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '思考档位' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: '关闭' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'thinking',
      'none'
    )
    await act(async () => {})
  })

  it('renders grouped model options with a group header and sends the nested value', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig()
    fireEvent.click(screen.getByRole('button', { name: '模型' }))
    expect(await screen.findByText('Kimi')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: 'K3 256k' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'model',
      'k3-256k'
    )
    await act(async () => {})
  })

  it('folds unknown / custom categories into the 高级设置 chip', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig()
    fireEvent.click(screen.getByRole('button', { name: '高级设置' }))
    expect(await screen.findByText('Sandbox')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: 'loose' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'sandbox',
      'loose'
    )
    await act(async () => {})
  })

  it('submits the exact id/value for advanced entries containing colons (#733 R4-P2)', async () => {
    // 后端契约只要求 config id 非空、不禁止冒号：提交必须是结构化载荷，
    // 不能从拼接的展示字符串拆回（拆回会把 id 截成错误前缀遭 400）。
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig(
      record({
        config_options: [
          {
            id: 'vendor:feature:flag',
            name: 'Feature',
            category: '_custom',
            type: 'select',
            currentValue: 'a:1',
            options: [{ value: 'a:1' }, { value: 'b:2', name: 'B 档' }],
          },
        ],
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '高级设置' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'B 档' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'vendor:feature:flag',
      'b:2'
    )
    await act(async () => {})
  })

  it('keeps menu keys unique for colon-containing ids (#733 R7-P2-a)', async () => {
    // id="a:b" 的组头与 id="a" + value="b" 的选项在旧编码下共享同一个
    // React key（entry:a:b）；菜单必须完整渲染两项、提交精确、无重复 key 告警。
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderWithConfig(
      record({
        config_options: [
          {
            id: 'a:b',
            name: 'AB',
            category: '_x',
            type: 'select',
            currentValue: 'v',
            options: [{ value: 'v' }],
          },
          {
            id: 'a',
            name: 'A',
            category: '_y',
            type: 'select',
            currentValue: 'b',
            options: [{ value: 'b', name: 'B 档' }],
          },
        ],
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '高级设置' }))
    expect(await screen.findByText('AB')).toBeInTheDocument()
    expect(screen.getByText('A')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: 'B 档' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'a',
      'b'
    )
    const keyWarnings = consoleSpy.mock.calls.filter((args) =>
      String(args[0]).includes('unique "key"')
    )
    consoleSpy.mockRestore()
    expect(keyWarnings).toHaveLength(0)
    await act(async () => {})
  })

  it('passes unknown native thought values through verbatim', async () => {
    mockApi.setStudioChatConfigOption.mockResolvedValue(record())
    renderWithConfig(
      record({
        config_options: [
          {
            ...KIMI_OPTIONS[1],
            options: [...KIMI_OPTIONS[1].options, { value: 'turbo' }],
          },
        ],
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '思考档位' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'turbo' }))
    expect(mockApi.setStudioChatConfigOption).toHaveBeenCalledWith(
      'ws1',
      's1',
      'thinking',
      'turbo'
    )
    await act(async () => {})
  })

  it('degrades a single-level thought list to a disabled chip', () => {
    renderWithConfig(
      record({
        config_options: [
          {
            ...KIMI_OPTIONS[1],
            currentValue: 'off',
            options: [{ value: 'off' }],
          },
        ],
      })
    )
    const chip = screen.getByRole('button', { name: '思考档位' })
    expect(chip).toBeDisabled()
    expect(chip).toHaveAttribute('title', '该模型不可调')
  })

  it('surfaces the server rejection and keeps the previous value', async () => {
    mockApi.setStudioChatMode.mockRejectedValue(
      new Error('Agent rejected the change: nope')
    )
    renderWithConfig()
    fireEvent.click(screen.getByRole('button', { name: 'Agent 权限模式' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Yolo' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Agent rejected the change: nope'
    )
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toHaveTextContent('Default')
  })

  it('announces thought-level drift after a model switch', async () => {
    const shifted = record({
      config_options: [
        { ...KIMI_OPTIONS[0], currentValue: 'k3-256k' },
        { ...KIMI_OPTIONS[1], currentValue: 'low' },
      ],
    })
    mockApi.setStudioChatConfigOption.mockResolvedValue(shifted)
    renderWithConfig()
    fireEvent.click(screen.getByRole('button', { name: '模型' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'K3 256k' }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        '思考档位已随模型切换变为 low'
      )
    )
  })

  it('disables every chip on a closed session, like the input box', () => {
    renderWithConfig(record({ status: 'closed' }))
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: '模型' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '思考档位' })).toBeDisabled()
  })
})

describe('StudioChatComposer context ring', () => {
  it('renders the context ring to the left of the model chip', () => {
    renderComposer({
      config: { workspaceId: 'ws1', session: record() },
      usage: { used: 1000, size: 2000 },
    })
    const ring = screen.getByLabelText('上下文用量')
    const modelChip = screen.getByRole('button', { name: '模型' })
    // 圆环在模型芯片左边：模型芯片在文档序上跟随圆环。
    expect(
      ring.compareDocumentPosition(modelChip) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('keeps the ring for agents that advertise no config surface', () => {
    renderComposer({
      config: {
        workspaceId: 'ws1',
        session: record({
          capability_snapshot: {},
          session_modes: null,
          config_options: null,
        }),
      },
      usage: { used: 1000, size: 2000 },
    })
    expect(screen.getByLabelText('上下文用量')).toBeInTheDocument()
  })

  it('renders the ring without the config prop too (diagnosis/preview)', () => {
    renderComposer({ usage: { used: 1000, size: 2000 } })
    expect(screen.getByLabelText('上下文用量')).toBeInTheDocument()
  })

  it('hides the ring when there is no usage and no compaction', () => {
    renderComposer()
    expect(screen.queryByLabelText('上下文用量')).not.toBeInTheDocument()
  })
})

describe('StudioChatComposer cancel button (#787)', () => {
  it('renders the cancel button next to the send button while running', () => {
    renderComposer({ busy: true, onCancel: vi.fn() })
    const cancelButton = screen.getByRole('button', { name: '取消' })
    const sendButton = screen.getByRole('button', { name: '排队' })
    // 取消在发送/排队按钮左边：发送按钮在文档序上跟随取消按钮。
    expect(
      cancelButton.compareDocumentPosition(sendButton) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('invokes onCancel on click', () => {
    const onCancel = vi.fn()
    renderComposer({ busy: true, onCancel })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('does not render the cancel button when not running', () => {
    renderComposer()
    expect(
      screen.queryByRole('button', { name: '取消' })
    ).not.toBeInTheDocument()
  })

  it('renders the status slot on the toolbar row, aligned with the buttons (#787)', () => {
    renderComposer({
      busy: true,
      onCancel: vi.fn(),
      statusSlot: <div aria-label="会话状态条">运行中</div>,
    })
    const strip = screen.getByLabelText('会话状态条')
    const sendButton = screen.getByRole('button', { name: '排队' })
    // 状态文本与按钮同一工具行（共同父元素，垂直居中由工具行
    // align-items:center 承担），且为行内最左子项。
    expect(strip.parentElement).toBe(sendButton.parentElement)
    expect(
      strip.compareDocumentPosition(sendButton) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })
})
