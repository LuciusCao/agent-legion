import { act, fireEvent, render, screen } from '@testing-library/react'
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

vi.mock('../../../components/SkillSelector', () => ({
  SkillSelector: ({ onChange }: { onChange: (value: string) => void }) => (
    <button
      data-testid="skill-selector-stub"
      onClick={() => onChange('demo/skill')}
    />
  ),
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

  it('opens the create form prefilled with the node capability', async () => {
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.click(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    )

    expect(await screen.findByDisplayValue('generate_key_info'))
    expect(mocks.fetchAgentDefinition).not.toHaveBeenCalled()
  })

  it('offers 切换为 Agent 执行 for a code node and switches the draft type on save', async () => {
    const onSwitchToAgent = vi.fn().mockReturnValue(true)
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    renderEditor({
      agentId: null,
      capability: 'generate_key_info',
      nodeType: 'code',
      onSwitchToAgent,
    })

    fireEvent.click(screen.getByRole('button', { name: '切换为 Agent 执行' }))
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    fireEvent.click(screen.getByTestId('skill-selector-stub'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '已切换为 Agent 执行，发布 workflow 后生效'
      )
    )
    expect(mocks.createAgentDefinition).toHaveBeenCalled()
    expect(onSwitchToAgent).toHaveBeenCalledTimes(1)
  })

  it('degrades to a manual-edit hint when the draft type switch fails', async () => {
    const onSwitchToAgent = vi.fn().mockReturnValue(false)
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    renderEditor({
      agentId: null,
      capability: 'generate_key_info',
      nodeType: 'code',
      onSwitchToAgent,
    })

    fireEvent.click(screen.getByRole('button', { name: '切换为 Agent 执行' }))
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    fireEvent.click(screen.getByTestId('skill-selector-stub'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        'Agent 草稿已创建；请手动在 YAML 将节点 type 改为 agent 并发布'
      )
    )
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
