import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { getSkillDetail } from '../../api/executorApi'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition, SkillDetail } from '../../types/executorTypes'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import { WorkflowSkillPreviewPanel } from './WorkflowSkillPreviewPanel'

// inspector 各 section（code/config/agent 执行详情）统一走 '../../api' 的 api。
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

// 默认响应不带 tags 字段：版本下拉降级为纯文本。带 tags 的场景各用例自行覆盖。
function skillDetail(overrides?: Partial<SkillDetail>): SkillDetail {
  return {
    key: 'demo/review',
    ref: 'v1.2.0',
    commit: 'abc1234567890',
    available: true,
    tags: ['v1.3.0', 'v1.2.0'],
    files: [
      { path: 'SKILL.md', size: 8, content: '# Skill', truncated: false },
    ],
    ...overrides,
  }
}

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

  it('lists skill tags in the version select and refetches with ?ref=', async () => {
    mockGetSkillDetail.mockImplementation((_key, ref) =>
      Promise.resolve(
        ref === 'v1.3.0'
          ? skillDetail({
              ref: 'v1.3.0',
              commit: 'def4567890123',
              files: [
                {
                  path: 'SKILL.md',
                  size: 11,
                  content: '# Skill v1.3',
                  truncated: false,
                },
              ],
            })
          : skillDetail()
      )
    )
    renderBody('generate_key_info')
    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))

    // 锁定版本为首项（标签带锁定 ref），tags 按响应倒序列出。
    fireEvent.mouseDown(await screen.findByLabelText('版本'))
    expect(
      await screen.findByRole('option', { name: '当前锁定版本（v1.2.0）' })
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: 'v1.3.0' }))

    expect(mockGetSkillDetail).toHaveBeenCalledWith('demo/review', 'v1.3.0')
    expect(await screen.findByText('# Skill v1.3')).toBeInTheDocument()
    // 查看中的 tag 与锁定版本标识清楚：下拉显示当前选中的 tag。
    expect(screen.getByRole('combobox')).toHaveTextContent('v1.3.0')
  })

  it('degrades the version select to plain text when the skill has no tags', async () => {
    mockGetSkillDetail.mockResolvedValue(skillDetail({ tags: [] }))
    renderBody('generate_key_info')
    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))

    expect(await screen.findByText('# Skill')).toBeInTheDocument()
    expect(screen.queryByLabelText('版本')).not.toBeInTheDocument()
    expect(screen.getByText('v1.2.0 · abc1234')).toBeInTheDocument()
  })

  it('resets the selected tag when the skill key changes', async () => {
    mockGetSkillDetail.mockImplementation((key, ref) =>
      Promise.resolve(
        ref === 'v1.3.0'
          ? skillDetail({
              key,
              ref: 'v1.3.0',
              files: [
                {
                  path: 'SKILL.md',
                  size: 11,
                  content: '# Skill v1.3',
                  truncated: false,
                },
              ],
            })
          : skillDetail({ key })
      )
    )
    const { rerender } = render(
      <TestQueryProvider>
        <WorkflowSkillPreviewPanel skillKey="demo/review" onBack={() => {}} />
      </TestQueryProvider>
    )
    fireEvent.mouseDown(await screen.findByLabelText('版本'))
    fireEvent.click(await screen.findByRole('option', { name: 'v1.3.0' }))
    expect(await screen.findByText('# Skill v1.3')).toBeInTheDocument()

    rerender(
      <TestQueryProvider>
        <WorkflowSkillPreviewPanel skillKey="demo/other" onBack={() => {}} />
      </TestQueryProvider>
    )

    // ref 选择带 skillKey 印记：切换技能即回落锁定版本（不带 ref 拉取）。
    expect(mockGetSkillDetail).toHaveBeenLastCalledWith('demo/other', undefined)
  })
})
