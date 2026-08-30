import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeDetailView } from './WorkflowNodeDetailView'

// inspector 各 section（code/config/agent 执行详情）统一走 '../../api' 的 api。
vi.mock('../../../api', () => ({ api: vi.fn() }))
// 技能预览经 agentCatalogApi wrapper（直连 './core'，不经 '../../api' 聚合层）。
vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn().mockResolvedValue({ agents: [] }),
  getSkillDetail: vi.fn(),
  getWorkspaceExecutionConfiguration: vi.fn(),
}))

vi.mock('./AgentEditor', () => ({
  AgentEditor: () => <div data-testid="agent-editor-stub" />,
}))

const mockApi = vi.mocked(api)
const mockGetSkillDetail = vi.mocked(getSkillDetail)

const workflow: WorkflowDefinitionRecord = {
  key: 'demo_workflow',
  label: 'Demo DAG',
  intake: { modes: [] },
  nodes: [
    {
      key: 'generate_key_info',
      label: '生成关键信息',
      capability: 'generate_key_info',
      after: [],
      inputs: ['questions.json'],
      outputs: ['key_info.json'],
    },
    {
      key: 'review',
      label: '评审',
      capability: 'review',
      after: ['generate_key_info'],
      inputs: ['key_info.json'],
      outputs: ['review.json'],
    },
  ],
  edges: [{ source: 'generate_key_info', target: 'review', condition: null }],
}

const agentCatalog: AgentDefinition[] = [
  {
    id: 'agent-key-info',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'demo/review',
    tools: ['read'],
    requires_labels: {},
    provider: 'deepseek',
    model: 'your-model-b',
    thinking: 'low',
    skill_ref: 'v1.2.0',
    skill_commit: 'abc1234567890',
  },
]

const definitionYaml = [
  'key: demo_workflow',
  'nodes:',
  '  generate_key_info:',
  '    capability: generate_key_info',
  '  review:',
  '    capability: review',
  '',
].join('\n')

function renderView(
  onBack: () => void = () => {},
  nodeKey = 'generate_key_info'
) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeDetailView
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={agentCatalog}
        definitionYaml={definitionYaml}
        setDefinitionYaml={() => {}}
        readOnly={false}
        agentOpen={false}
        onToggleAgent={() => {}}
        onBack={onBack}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.mockResolvedValue({})
    mockGetSkillDetail.mockResolvedValue({
      key: 'demo/review',
      ref: 'v1.2.0',
      commit: 'abc1234567890',
      available: true,
      files: [
        { path: 'SKILL.md', size: 8, content: '# Skill', truncated: false },
      ],
    })
  })

  it('shows the plain breadcrumb and backs out to the DAG by default', () => {
    const onBack = vi.fn()
    renderView(onBack)

    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回 DAG' }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('deepens the breadcrumb in the prompt preview and the back button steps back to the node details', () => {
    const onBack = vi.fn()
    renderView(onBack)

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))

    expect(
      screen.getByText('Demo DAG / 生成关键信息 / Prompt')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(onBack).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回 DAG' }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('deepens the breadcrumb in the skill preview', async () => {
    renderView()

    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))

    expect(
      screen.getByText('Demo DAG / 生成关键信息 / 技能文件')
    ).toBeInTheDocument()
    expect(await screen.findByText('# Skill')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(screen.queryByLabelText('技能文件预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
  })

  it('resets the preview when the selected node changes', () => {
    const { rerender } = renderView()
    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(
      screen.getByText('Demo DAG / 生成关键信息 / Prompt')
    ).toBeInTheDocument()

    const viewFor = (key: string) => (
      <TestQueryProvider>
        <WorkflowNodeDetailView
          workflow={workflow}
          nodeKey={key}
          agentCatalog={agentCatalog}
          definitionYaml={definitionYaml}
          setDefinitionYaml={() => {}}
          readOnly={false}
          agentOpen={false}
          onToggleAgent={() => {}}
          onBack={() => {}}
        />
      </TestQueryProvider>
    )

    rerender(viewFor('review'))

    // nodeKey 变化即真正清除预览：切走后落在节点详情。
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 评审')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回 DAG' })).toBeInTheDocument()

    // 切回原节点也不恢复预览（仍是节点详情，不是挂起态）。
    rerender(viewFor('generate_key_info'))
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回 DAG' })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
  })
})
