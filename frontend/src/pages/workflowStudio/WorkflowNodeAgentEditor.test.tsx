import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useSettingStore } from '../../stores/settingStore'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'

const mocks = {
  fetchAgentDefinition: vi.fn(),
  createAgentDefinition: vi.fn(),
  saveAgentDraft: vi.fn(),
  publishAgent: vi.fn(),
  archiveAgent: vi.fn(),
}

vi.mock('../../api', () => ({
  fetchAgentDefinition: (...args: unknown[]) =>
    mocks.fetchAgentDefinition(...args),
  createAgentDefinition: (...args: unknown[]) =>
    mocks.createAgentDefinition(...args),
  saveAgentDraft: (...args: unknown[]) => mocks.saveAgentDraft(...args),
  publishAgent: (...args: unknown[]) => mocks.publishAgent(...args),
  archiveAgent: (...args: unknown[]) => mocks.archiveAgent(...args),
}))

vi.mock('../../components/SkillSelector', () => ({
  SkillSelector: () => <div data-testid="skill-selector-stub" />,
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
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
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
