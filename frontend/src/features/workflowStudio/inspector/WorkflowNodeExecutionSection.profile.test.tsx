import { fireEvent, render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import { useSettingStore } from '../../../stores/settingStore'
import { fetchAgentDefinitions } from '../../../api/agentDefinitions'
import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn().mockResolvedValue({ agents: [] }),
}))

// #387：draft-only Agent 的节点解析回落 agent-definitions（含 draft）。
vi.mock('../../../api/agentDefinitions', () => ({
  fetchAgentDefinitions: vi.fn(),
}))
vi.mock('../../../stores/settingStore', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../stores/settingStore')>()
  return actual
})

// #935：自含节点不渲染 AgentEditor；stub 用于断言其缺席/在场。
vi.mock('./AgentEditor', () => ({
  AgentEditor: () => <div data-testid="agent-editor-stub" />,
}))

// 节点 skill 编辑行的交互由 WorkflowNodeSkillEditor.test.tsx 覆盖；此处 stub
// 掉带真实 API 的 SkillSelector，只验证 section 的渲染分发。
vi.mock('../../../components/SkillSelector', () => ({
  SkillSelector: () => <div data-testid="skill-selector-stub" />,
}))

// 「继承默认」提示来自草稿 YAML 顶层 execution 块；datalist 选项来自
// useWorkspaceRuntimeModels（在线 Worker 声明的 runtime/provider/model）。
vi.mock('../shared/useWorkspaceRuntimeModels', () => ({
  useWorkspaceRuntimeModels: () => ({
    data: {
      runtimes: {
        pi: { deepseek: ['your-model-b', 'your-model-c'] },
      },
    },
  }),
}))

const node: WorkflowNodeRecord = {
  key: 'generate_key_info',
  label: '生成关键信息',
  capability: 'generate_key_info',
  // 显式 Agent 节点（#284）：类型判定只读 node_type，不再按 capability 反推。
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
  terminal: null,
}

const agentCatalog: AgentDefinition[] = [
  {
    id: 'question-key-info-v1',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'demo_workflow/generate_key_info',
    tools: ['read', 'write', 'bash'],
    requires_labels: {},
    provider: 'deepseek',
    model: 'your-model-b',
    thinking: 'low',
    skill_ref: 'v1.3.8',
    skill_commit: '5c5eae72064abde37bfc4b07a4b2f7e9637c473d',
  },
]

const editorProps = {
  definitionYaml: `execution:\n  provider: deepseek\n  model: your-model-b\n  thinking: low\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
  setDefinitionYaml: () => {},
  agentCatalog,
}

// #426 codex 终轮 P2：settle 信号的工厂——两份查询均 settle 的基线，各
// 门控用例按场景覆盖（catalog 在途/失败、definitions 在途/失败）。
const settledSettle = {
  catalogSettled: true,
  catalogFailed: false,
  definitionsSettled: true,
  definitionsFailed: false,
}

function renderSection(
  props: Omit<
    React.ComponentProps<typeof WorkflowNodeExecutionSection>,
    'agentCatalogSettle'
  > &
    Partial<
      Pick<
        React.ComponentProps<typeof WorkflowNodeExecutionSection>,
        'agentCatalogSettle'
      >
    >
) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={
            <>
              {/* #426 review P2：默认两份查询均 settle（本套件聚焦 section
                  分发，加载/错误占位的组合逻辑由 agentBindingStatus.test.ts
                  与 WorkflowNodeAgentEditor.test.tsx 覆盖）。 */}
              <WorkflowNodeExecutionSection
                agentCatalogSettle={settledSettle}
                {...props}
              />
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowNodeExecutionSection node profile (#935)', () => {
  beforeEach(() => {
    useSettingStore.setState({ workspaceId: 'ws1' })
    vi.mocked(fetchAgentDefinitions).mockResolvedValue({ agents: [] })
  })

  const selfContainedYaml = `nodes:\n  generate_key_info:\n    type: agent\n    capability: generate_key_info\n    execution:\n      runtime: velites\n`

  it('edits a self-contained node profile without the Agent editor', () => {
    renderSection({
      node,
      agentCatalog,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
    expect(screen.queryByText(/尚未内联执行档案/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(screen.getByTestId('skill-selector-stub')).toBeInTheDocument()
    expect(screen.getByLabelText('Model')).toBeInTheDocument()
  })

  it('treats the workflow top-level runtime as a self-contained profile', () => {
    renderSection({
      node,
      agentCatalog,
      definitionYaml: `execution:\n  runtime: pi\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
      setDefinitionYaml: () => {},
    })

    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
    expect(screen.getByText(/继承 workflow 默认（pi）/)).toBeInTheDocument()
  })

  it('writes the selected runtime onto the node execution block', () => {
    let nextYaml = ''
    renderSection({
      node,
      agentCatalog,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: (value) => {
        nextYaml = value
      },
    })

    fireEvent.mouseDown(screen.getByLabelText('Runtime'))
    fireEvent.click(screen.getByRole('option', { name: 'pi' }))

    expect(nextYaml).toContain('runtime: pi')
  })

  it('flags a legacy (not yet inlined) node and keeps the Agent editor for reference', () => {
    renderSection({ node, ...editorProps })

    expect(screen.getByText(/尚未内联执行档案/)).toBeInTheDocument()
    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(screen.getByTestId('agent-editor-stub')).toBeInTheDocument()
  })
})
