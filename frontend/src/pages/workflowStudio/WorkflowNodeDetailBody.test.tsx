import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { getSkillDetail } from '../../api/executorApi'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'

// inspector 各 section（code/config/agentDefaults）统一走 '../../api' 的 api。
vi.mock('../../api', () => ({ api: vi.fn() }))
// 技能预览经 executorApi wrapper（直连 './core'，不经 '../../api' 聚合层）。
vi.mock('../../api/executorApi', () => ({
  getExecutorCatalog: vi.fn().mockResolvedValue({ agents: [] }),
  getSkillDetail: vi.fn(),
  getWorkspaceExecutorConfiguration: vi.fn(),
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
  {
    id: 'agent-review',
    runtime: 'pi',
    capability: 'review',
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

function renderBody(nodeKey: string) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeDetailBody
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={agentCatalog}
        definitionYaml={definitionYaml}
        setDefinitionYaml={() => {}}
        readOnly={false}
        onClose={() => {}}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeDetailBody', () => {
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
        {
          path: 'references/rules.md',
          size: 7,
          content: '# Rules',
          truncated: false,
        },
      ],
    })
  })

  it('switches the panel to the in-place skill preview instead of a dialog', async () => {
    renderBody('generate_key_info')

    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))

    // panel 原位替换：不出 dialog，inspector 内容让位给预览视图。
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByText('# Skill')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '查看 Prompt' })
    ).not.toBeInTheDocument()
    // 版本展示：lock 的当前版本（ref · commit 短 sha）。
    expect(screen.getByText('v1.2.0 · abc1234')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith('demo/review', undefined)

    fireEvent.click(
      screen.getByRole('button', { name: /references\/rules.md/ })
    )
    expect(screen.getByText('# Rules')).toBeInTheDocument()
  })

  it('returns to the node details from the skill preview', async () => {
    renderBody('generate_key_info')

    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))
    expect(await screen.findByText('# Skill')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))

    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('技能文件预览')).not.toBeInTheDocument()
  })

  it('shows the prompt preview in place and returns', async () => {
    renderBody('generate_key_info')

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()
    expect(screen.getByText('生成关键信息 · 运行 Prompt')).toBeInTheDocument()
    expect(
      screen.getByText(/Node: generate_key_info/, { exact: false })
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(
      screen.getByRole('button', { name: '浏览技能文件' })
    ).toBeInTheDocument()
  })

  it('resets the preview when the selected node changes', async () => {
    const { rerender } = renderBody('generate_key_info')
    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()

    rerender(
      <TestQueryProvider>
        <WorkflowNodeDetailBody
          workflow={workflow}
          nodeKey="review"
          agentCatalog={agentCatalog}
          definitionYaml={definitionYaml}
          setDefinitionYaml={() => {}}
          readOnly={false}
          onClose={() => {}}
        />
      </TestQueryProvider>
    )

    // 预览状态带 nodeKey 印记：切换选中节点即回到节点详情。
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
  })
})
