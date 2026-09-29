import { createElement, type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobDiagnosisPanel } from './JobDiagnosisPanel'
import * as chatApi from '../workflowStudio/chat/studioChatApi'
import * as configApi from '../workflowStudio/chat/studioChatConfigApi'
import '../workflowStudio/chat/studioChatResumeApi'
import * as jobApi from '../../api/jobApi'
import type { StudioChatSessionRecord } from '../workflowStudio/chat/studioChatApi'
import { EventSourceMock } from '../../testing/eventSourceMock'
import { createTestQueryClient } from '../../testing/testQueryClient'
import { expectConsoleError } from '../../test-setup'

vi.mock('../workflowStudio/chat/studioChatApi')
vi.mock('../workflowStudio/chat/studioChatConfigApi')
vi.mock('../workflowStudio/chat/studioChatResumeApi')
vi.mock('../../api/jobApi', () => ({
  rerunJob: vi.fn(),
  runToJob: vi.fn(),
}))

const mockApi = vi.mocked(chatApi)
const mockConfigApi = vi.mocked(configApi)
const mockJobApi = vi.mocked(jobApi)

const TARGET = {
  workspaceId: 'ws1',
  jobId: 'job-1',
  nodeKey: 'write_script',
  nodeLabel: '撰写脚本',
}

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's9',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: null,
    capability_snapshot: {},
    allow_all_permissions: false,
    compacting: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function agentTextMessage(id: string, seq: number, text: string) {
  return {
    id,
    session_id: 's9',
    kind: 'text' as const,
    role: 'agent' as const,
    content: { text },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('JobDiagnosisPanel', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.createStudioChatSession.mockResolvedValue(sessionRecord())
    mockApi.sendStudioChatMessage.mockImplementation((_ws, _session, text) =>
      Promise.resolve({
        id: 'u1',
        session_id: 's9',
        kind: 'text',
        role: 'user',
        content: { text },
        seq: 1,
        created_at: '2026-01-01T00:00:00Z',
      })
    )
    mockJobApi.rerunJob.mockResolvedValue({
      job_id: 'job-1',
      operation: 'rerun',
      status: 'succeeded',
    })
    mockJobApi.runToJob.mockResolvedValue({
      job_id: 'job-1',
      operation: 'run_to',
      status: 'succeeded',
    })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  function renderPanel() {
    return render(<JobDiagnosisPanel workspaceId="ws1" target={TARGET} />, {
      wrapper,
    })
  }

  async function renderReadyPanel() {
    const view = renderPanel()
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalledWith(
        'ws1',
        'kimi'
      )
    )
    await waitFor(() =>
      expect(mockApi.sendStudioChatMessage).toHaveBeenCalled()
    )
    await waitFor(() =>
      expect(EventSourceMock.instances.length).toBeGreaterThan(0)
    )
    return view
  }

  function emit(payload: object) {
    const source =
      EventSourceMock.instances[EventSourceMock.instances.length - 1]
    expect(source).toBeDefined()
    act(() => source!.emitMessage(payload))
  }

  it('auto-creates the session and injects the workspace+job+node context', async () => {
    await renderReadyPanel()
    const primer = mockApi.sendStudioChatMessage.mock.calls[0][2]
    expect(primer).toContain('workspace_id: ws1')
    expect(primer).toContain('job_id: job-1')
    expect(primer).toContain('关注节点: write_script')
    expect(primer).toContain('get_job_context')
  })

  it('creates exactly one session per panel mount', async () => {
    await renderReadyPanel()
    expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1)
    expect(mockApi.sendStudioChatMessage).toHaveBeenCalledTimes(1)
  })

  const configRecord = () =>
    sessionRecord({
      capability_snapshot: { sessionModes: true, sessionConfigOptions: true },
      session_modes: {
        currentModeId: 'default',
        availableModes: [{ id: 'default', name: 'Default' }],
      },
      config_options: [
        {
          id: 'model',
          name: 'Model',
          category: 'model',
          type: 'select',
          currentValue: 'k3',
          options: [{ value: 'k3', name: 'K3' }],
        },
        {
          id: 'thinking',
          name: 'Thinking',
          category: 'thought_level',
          type: 'select',
          currentValue: 'high',
          options: [{ value: 'low' }, { value: 'high' }],
        },
      ],
    } as never)

  it('chips 常驻排查 composer（#795 收尾）：权限/模型/思考芯片可见可交互', async () => {
    // 排查会话与 studio 共用 useStudioChat（#329），kimi ACP 握手广告面
    // 同款——showAgentConfig 接上后工具行即显示配置芯片。
    mockApi.createStudioChatSession.mockResolvedValue(configRecord())
    await renderReadyPanel()
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Agent 权限模式' })
      ).toBeEnabled()
    )
    expect(screen.getByRole('button', { name: '模型' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '思考档位' })).toBeInTheDocument()
  })

  it('引导期间（create 未落地）chips 已可见不空窗：回落历史会话展示（#796 R3 继承）', async () => {
    // 排查线实测语义：useStudioChat 会话记忆自动恢复最近会话，引导期
    // chat.session 非空——chips 锚定真实历史会话，不走严格 readOnly 路径；
    // 严格 readOnly（无会话可恢复）由 composer 级既有用例钉住
    // （StudioChatComposer.test 的 #796 R3 用例）。
    mockApi.fetchStudioChatSessions.mockResolvedValue([configRecord()])
    mockApi.createStudioChatSession.mockImplementation(
      () => new Promise<never>(() => {})
    )
    renderPanel()
    // 不空窗契约 = chips 存在；可交互与否取决于恢复竞态（会话恢复完成则
    // 可交互，未完成则走 readOnly 回落只读）——两条路径都合法，只断言存在
    // （CI 与本地调度时序不同，断言 enabled 会抖动）。
    await screen.findByRole('group', { name: 'Agent 配置' })
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '模型' })).toBeInTheDocument()
  })

  it('bootstrap 在途 chips 强制只读，落地后变更打到新建会话（#801 codex 轮 4 P2）', async () => {
    // MUI Menu 开合驱动芯片组状态更新脱离 act（known noise，同既有用例）。
    expectConsoleError(/not wrapped in act/)
    // 历史会话存在 + create 挂起：chips 必须禁用——否则点模型/权限/思考会
    // 经 useStudioChatAgentConfig 提交到历史会话 ID（revert：不禁用，即红）。
    mockApi.fetchStudioChatSessions.mockResolvedValue([configRecord()])
    // 会话详情 mock 给真实返回：默认 vi.fn() 返回 undefined，恢复/激活
    // 链路在 CI 调度时序下会走错误路径（actionError 置位后 primer 永不发）。
    mockApi.fetchStudioChatSession.mockImplementation(
      (_ws: string, id: string) =>
        Promise.resolve({ ...configRecord(), id } as StudioChatSessionRecord)
    )
    let resolveCreate: (session: StudioChatSessionRecord) => void = () => {}
    mockApi.createStudioChatSession.mockImplementation(
      () =>
        new Promise<StudioChatSessionRecord>((resolve) => {
          resolveCreate = resolve
        })
    )
    mockConfigApi.setStudioChatMode.mockResolvedValue(configRecord())
    renderPanel()
    await screen.findByRole('group', { name: 'Agent 配置' })
    // 等 boot 真正发起 create 再 resolve——CI 调度慢时 boot 可能晚于本行，
    // 过早 resolve 会打到初始 no-op（create 稍后才发起、promise 永挂）。
    await waitFor(
      () => expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1),
      { timeout: 5000 }
    )
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: '模型' })).toBeDisabled()

    // bootstrap 落地：新建会话激活，chips 恢复可交互。
    const newSession = {
      ...configRecord(),
      id: 's-new',
      session_modes: {
        currentModeId: 'default',
        availableModes: [
          { id: 'default', name: 'Default' },
          { id: 'plan', name: 'Plan' },
        ],
      },
    } as StudioChatSessionRecord
    await act(async () => {
      resolveCreate(newSession)
    })
    // 激活信号锚定 primer（新会话落地且 idle 才发）——CI 并行调度下
    // create 之后的链路刷新可能慢，1s 默认超时不够，放宽到 5s。
    await waitFor(
      () => expect(mockApi.sendStudioChatMessage).toHaveBeenCalled(),
      { timeout: 5000 }
    )
    await waitFor(
      () =>
        expect(
          screen.getByRole('button', { name: 'Agent 权限模式' })
        ).toBeEnabled(),
      { timeout: 5000 }
    )

    // 变更打到新建会话 ID，不是历史会话。
    fireEvent.click(screen.getByRole('button', { name: 'Agent 权限模式' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Plan' }))
    expect(mockConfigApi.setStudioChatMode).toHaveBeenCalledWith(
      'ws1',
      's-new',
      'plan'
    )
  })

  it('bootstrap 失败：chips 保持只读且创建错误如实呈现（#801 codex 轮 5 P2）', async () => {
    // 历史会话存在 + create reject：starting 归 false 后历史会话保留——
    // 解锁条件必须是「本次新建的会话已激活」，不能是 starting 回落
    // （revert 回 configReadOnly={chat.starting}：chips 重新可编辑历史会话
    // 且错误被 chat.session 非空隐藏，即红）。
    mockApi.fetchStudioChatSessions.mockResolvedValue([configRecord()])
    mockApi.createStudioChatSession.mockRejectedValue(new Error('gateway 503'))
    renderPanel()
    await screen.findByRole('group', { name: 'Agent 配置' })
    await waitFor(
      () => expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1),
      { timeout: 5000 }
    )
    // chips 保持禁用（历史会话不得被误改）。
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Agent 权限模式' })
      ).toBeDisabled()
    )
    expect(screen.getByRole('button', { name: '模型' })).toBeDisabled()
    // 创建失败如实呈现（带重试入口），不静默表现为「可编辑的旧会话」。
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('排查会话创建失败')
    expect(alert).toHaveTextContent('gateway 503')
  })

  it('引导失败后重试成功：解锁、primer 发送、错误清除（#801 codex 轮 6 P2）', async () => {
    // 首次 create reject、重试 resolve：重试必须清掉上一次的 actionError
    // 残留——否则重试成功帧上旧错误被失败闩锁误采，新会话永久锁定、primer
    // 不发（revert 掉 clearActionError 调用即红）。
    // 时序构造（让残留活到重试落定帧）：agents 列表挂起让恢复先选中历史
    // 会话，失败帧无会话切换（不触发消息加载 effect 的清错），旧错误活到
    // 重试成功帧。
    mockApi.fetchStudioChatSessions.mockResolvedValue([configRecord()])
    let resolveAgents: (
      agents: { id: string; label: string }[]
    ) => void = () => {}
    mockApi.fetchStudioChatAgents.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveAgents = resolve
        })
    )
    const newSession = {
      ...configRecord(),
      id: 's-new',
    } as StudioChatSessionRecord
    mockApi.createStudioChatSession
      .mockRejectedValueOnce(new Error('gateway 503'))
      .mockResolvedValue(newSession)
    renderPanel()
    // 恢复先落地：历史会话被选中。
    await screen.findByRole('group', { name: 'Agent 配置' })

    // agents 到达 → boot 发起 → 首次 create reject。引导失败条与底层
    // actionError 条都是 role=alert——按文案锚定引导失败条（后者会先出现）。
    await act(async () => {
      resolveAgents([{ id: 'kimi', label: 'Kimi Code' }])
    })
    await screen.findByText(/排查会话创建失败：gateway 503/)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重试' }))
    })
    // 重试成功：primer 发出（新会话激活且 idle 的信号）→ chips 解锁 →
    // 错误条消失。
    await waitFor(
      () => expect(mockApi.sendStudioChatMessage).toHaveBeenCalled(),
      { timeout: 5000 }
    )
    await waitFor(
      () =>
        expect(
          screen.getByRole('button', { name: 'Agent 权限模式' })
        ).toBeEnabled(),
      { timeout: 5000 }
    )
    await waitFor(() =>
      expect(screen.queryByText(/排查会话创建失败|gateway 503/)).toBeNull()
    )
  })

  it('inDock 换用无底尺寸的外壳类（#800 codex P2：Dock 里 320px min-height 会裁掉 composer）', async () => {
    // vitest 的 CSS modules 把类名解析为带 hash 的键名（_chatShellDock_xxx）
    // ——按子串断言变体切换（revert：inDock 也用 chatShell（带
    // min-height:320），即红）。
    const view = render(
      <JobDiagnosisPanel workspaceId="ws1" target={TARGET} inDock />,
      { wrapper }
    )
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalled()
    )
    const shell = view.container.querySelector('[class*="chatShell"]')
    expect(shell).not.toBeNull()
    expect(shell!.className).toContain('chatShellDock')

    // 旧 Dialog 宿主（默认 inDock=false）保持 320 底尺寸的 chatShell 不变。
    const dialogView = renderPanel()
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(2)
    )
    const dialogShell = dialogView.container.querySelector(
      '[class*="chatShell"]'
    )
    expect(dialogShell!.className).toContain('chatShell')
    expect(dialogShell!.className).not.toContain('chatShellDock')
  })

  it('renders the suggested action as a confirm card and executes on confirm', async () => {
    await renderReadyPanel()
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')

    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        [
          '诊断：节点超时。',
          '```json',
          '{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-1", "node_key": "write_script", "reason": "超时重试即可"}}',
          '```',
        ].join('\n')
      ),
    })

    const card = await screen.findByRole('group', {
      name: '建议动作 重跑节点 write_script',
    })
    expect(card).toHaveTextContent('超时重试即可')

    // 执行前不碰动作端点。
    expect(mockJobApi.rerunJob).not.toHaveBeenCalled()
    act(() => {
      screen.getByRole('button', { name: '确认执行' }).click()
    })
    await waitFor(() =>
      expect(mockJobApi.rerunJob).toHaveBeenCalledWith('job-1', 'write_script')
    )
    await waitFor(() => expect(card).toHaveTextContent('已执行'))
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['jobDetail', 'job-1'],
    })
  })

  it('dismisses a suggestion without executing', async () => {
    await renderReadyPanel()
    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        '```json\n{"job_action_suggestion": {"action": "run_to_node", "job_id": "job-1", "node_key": "review_script"}}\n```'
      ),
    })
    await screen.findByRole('group', {
      name: '建议动作 重跑至节点 review_script',
    })
    act(() => {
      screen.getByRole('button', { name: '忽略' }).click()
    })
    expect(mockJobApi.runToJob).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(
        screen.queryByRole('group', {
          name: '建议动作 重跑至节点 review_script',
        })
      ).not.toBeInTheDocument()
    )
  })

  it('ignores suggestions pointing at another job', async () => {
    await renderReadyPanel()
    emit({
      type: 'message',
      message: agentTextMessage(
        'm1',
        2,
        '```json\n{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-OTHER", "node_key": "x"}}\n```'
      ),
    })
    // 等一拍确认没有卡片渲染（findBy 会等到超时，这里用固定帧 + query）。
    await act(async () => {
      await Promise.resolve()
    })
    expect(screen.queryByRole('group', { name: /建议动作/ })).toBeNull()
  })

  it('renders the resume bar when the session lands in error (#558)', async () => {
    // #558：闲置后工具通道死亡的会话升级为 error——排查面板必须给
    // 「继续对话」入口（此前只有禁用文案，会话只能废弃）。
    mockApi.createStudioChatSession.mockResolvedValue(
      sessionRecord({ status: 'error' })
    )
    mockApi.sendStudioChatMessage.mockResolvedValue({
      id: 'u1',
      session_id: 's9',
      kind: 'text',
      role: 'user',
      content: { text: 'hi' },
      seq: 1,
      created_at: '2026-01-01T00:00:00Z',
    })
    renderPanel()
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalled()
    )
    await screen.findByRole('button', { name: '继续对话' })
    expect(screen.getByText(/会话已中断，历史记录已保留/)).toBeInTheDocument()
  })

  it('does not render the resume bar while the session is live', async () => {
    await renderReadyPanel()
    expect(
      screen.queryByRole('button', { name: '继续对话' })
    ).not.toBeInTheDocument()
  })
})
