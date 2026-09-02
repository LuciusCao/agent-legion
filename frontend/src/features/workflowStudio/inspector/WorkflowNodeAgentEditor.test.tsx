import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'

const mocks = {
  fetchAgentDefinition: vi.fn(),
  createAgentDefinition: vi.fn(),
  saveAgentDraft: vi.fn(),
  publishAgent: vi.fn(),
  archiveAgent: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentDefinition: (...args: unknown[]) =>
    mocks.fetchAgentDefinition(...args),
  createAgentDefinition: (...args: unknown[]) =>
    mocks.createAgentDefinition(...args),
  saveAgentDraft: (...args: unknown[]) => mocks.saveAgentDraft(...args),
  publishAgent: (...args: unknown[]) => mocks.publishAgent(...args),
  archiveAgent: (...args: unknown[]) => mocks.archiveAgent(...args),
}))

function renderEditor(
  props: React.ComponentProps<typeof WorkflowNodeAgentEditor>
) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeAgentEditor {...props} />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeAgentEditor', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    const { useUiStore } = await import('../../../stores/uiStore')
    useUiStore.setState({ toast: null })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: null,
      published: {
        status: 'published',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
    })
  })

  it('opens the embedded editor for the bound agent and loads its definition', async () => {
    renderEditor({ agentId: 'agent-a', capability: 'generate_key_info' })

    fireEvent.click(screen.getByRole('button', { name: '编辑 Agent' }))

    expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith('ws1', 'agent-a')
    expect(await screen.findByDisplayValue('generate_key_info'))
    expect(screen.getByDisplayValue('agent-a')).toBeInTheDocument()
  })

  it('keeps the loaded skill when editing an existing agent (#76: legacy fallback)', async () => {
    mocks.saveAgentDraft.mockResolvedValue({})
    renderEditor({ agentId: 'agent-a', capability: 'generate_key_info' })

    fireEvent.click(screen.getByRole('button', { name: '编辑 Agent' }))
    await screen.findByDisplayValue('generate_key_info')
    // 定义加载自带 skill 的存量 Agent：编辑器不展示 skill，但保存时原样保留
    // （节点未绑 skill 的 workflow 仍靠 AgentDefinition.skill 兜底）。
    expect(screen.queryByDisplayValue('demo/skill')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    // waitFor 的轮询包在 act 里：保存 resolve 后的 busy/toast 状态更新被覆盖。
    await waitFor(() =>
      expect(mocks.saveAgentDraft).toHaveBeenCalledWith(
        'ws1',
        'agent-a',
        expect.objectContaining({
          capability: 'generate_key_info',
          skill: 'demo/skill',
        })
      )
    )
  })

  it('creates a new agent with an empty skill (node-level binding)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.click(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    )
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    expect(mocks.createAgentDefinition).toHaveBeenCalledWith(
      'ws1',
      expect.objectContaining({ agent_id: 'agent-new', skill: '' })
    )
  })

  // #387：普通新建（非 switchToAgent）创建的是 draft-only Agent，目录里
  // 查不到；关面板会让「发布」按钮永远不可达——创建后面板必须留在编辑/
  // 发布模式。
  it('stays open in publish mode after a plain create (not just switchToAgent)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.click(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    )
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      // AgentEditor 的创建 toast（Panel 不再叠加第二条，subagent P3）。
      expect(useUiStore.getState().toast?.message).toBe(
        'Agent「agent-new」草稿已创建'
      )
    )
    // 面板不关：切到编辑/发布模式加载新草稿，「发布」按钮可达。
    await vi.waitFor(() =>
      expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith(
        'ws1',
        'agent-new'
      )
    )
    expect(await screen.findByRole('button', { name: '发布' })).toBeEnabled()
    // 入口按钮同步切到编辑态文案。
    expect(
      screen.getByRole('button', { name: '收起 Agent 编辑' })
    ).toBeInTheDocument()
  })

  it('opens the create form prefilled with the node capability', async () => {
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.click(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    )

    expect(await screen.findByDisplayValue('generate_key_info'))
    expect(mocks.fetchAgentDefinition).not.toHaveBeenCalled()
  })

  it('stays open in publish mode after creating a draft agent (agent node entry, #392)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.click(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    )
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    expect(mocks.createAgentDefinition).toHaveBeenCalled()
    // 面板不关：切到编辑/发布模式加载新草稿（/api/agent-catalog 只回
    // published，直接关面板会让用户无法从该入口发布它）。
    await vi.waitFor(() =>
      expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith(
        'ws1',
        'agent-new'
      )
    )
    expect(await screen.findByRole('button', { name: '发布' })).toBeEnabled()
  })

  it('renders nothing in read-only mode or without a workspace', () => {
    const { container, unmount } = renderEditor({
      agentId: 'agent-a',
      capability: 'cap',
      readOnly: true,
    })
    expect(container).toBeEmptyDOMElement()
    // 先卸载再改全局 store，避免已挂载订阅者在 act 外更新。
    unmount()

    useSettingStore.setState({ workspaceId: null })
    const { container: noWorkspace } = renderEditor({
      agentId: 'agent-a',
      capability: 'cap',
    })
    expect(noWorkspace).toBeEmptyDOMElement()
  })
})
