import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useUiStore } from '../../../stores/uiStore'
import { AgentDefinitionDraftCard } from '../chat/StudioChatDraftCards'
import type { AgentDefinitionDraftView } from '../chat/studioChatMessages'
import { WorkflowStudioPageContent } from './WorkflowStudioPageContent'
import type { useWorkflowStudio } from './useWorkflowStudio'

// #387：openAgent 的导航行为——draft-only Agent 经 agent-definitions 回落
// 定位到节点；空 workflow（无 capability 绑定）给明确提示而非静默空转。
// Layout 拉起整个画布树，这里 stub 成 StudioNavContext 的直接消费者。

type Studio = ReturnType<typeof useWorkflowStudio>

const agentCatalog: Studio['agentCatalog'] = []

const agentDefinitions: Studio['agentDefinitions'] = [
  {
    agent_id: 'draft-agent',
    capability: 'generate_key_info',
    runtime: 'pi',
    skill: '',
    version: 1,
    status: 'draft',
    has_draft: true,
    published_at: null,
  },
]

const node = {
  key: 'generate_key_info',
  label: '生成关键信息',
  capability: 'generate_key_info',
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
  terminal: null,
}

const workflow = {
  key: 'demo',
  label: 'Demo',
  nodes: [node],
  edges: [],
} as unknown as Studio['workflow']

const draftView: AgentDefinitionDraftView = {
  toolCallId: 'call-1',
  agentId: 'draft-agent',
  capability: 'generate_key_info',
  runtime: 'pi',
  skill: null,
}

function makeStudio(overrides: Partial<Studio> = {}): Studio {
  return {
    workflow: null,
    agentCatalog,
    agentDefinitions,
    setSelectedNodeKey: vi.fn(),
    ...overrides,
  } as unknown as Studio
}

// Layout stub：把草稿卡片挂进 NavProvider 子树，模拟聊天面板的挂载位置。
vi.mock('./WorkflowStudioLayout', () => ({
  WorkflowStudioLayout: () => <AgentDefinitionDraftCard draft={draftView} />,
}))

function renderPage(studio: Studio) {
  return render(
    <TestQueryProvider>
      <WorkflowStudioPageContent studio={studio} />
    </TestQueryProvider>
  )
}

describe('WorkflowStudioPageContent openAgent', () => {
  it('selects the node bound to a draft-only agent via agent-definitions', async () => {
    const studio = makeStudio({ workflow, setSelectedNodeKey: vi.fn() })
    renderPage(studio)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看草稿' }))
    })

    await waitFor(() =>
      expect(studio.setSelectedNodeKey).toHaveBeenCalledWith(
        'generate_key_info'
      )
    )
  })

  it('shows a toast instead of silently doing nothing when no node binds the capability (empty workflow)', async () => {
    const studio = makeStudio({ workflow: null })
    useUiStore.setState({ toast: null })
    renderPage(studio)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看草稿' }))
    })

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '当前 workflow 草稿中没有绑定该 Agent capability 的节点'
      )
    )
    expect(studio.setSelectedNodeKey).not.toHaveBeenCalled()
  })
})
