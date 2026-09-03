import yaml from 'js-yaml'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { NODE_TYPE_SECTIONS } from './nodeTypeSections'
import { api } from '../../../api'
import { useSettingStore } from '../../../stores/settingStore'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('../../../api', () => ({ api: vi.fn() }))

const mockApi = vi.mocked(api)

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}

// 三类型 DAG：gate 是中游审批门（after intake）。
const dagYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    label: 读取知识点',
  '    capability: intake',
  '    after: [_start]',
  '  gate:',
  '    type: approval',
  '    label: 人工把关',
  '    config: {rework_target: intake, feedback_artifact: review.json}',
  '    after: [intake]',
  'edges:',
  '  - from: _start',
  '    to: intake',
  '',
].join('\n')

function renderInspector(nodeKey: string) {
  return render(
    <WorkflowNodeInspector
      workflow={null}
      agentCatalog={[]}
      agentBindingStatus="ready"
      selectedNodeKey={nodeKey}
      definitionYaml={dagYaml}
      setDefinitionYaml={() => {}}
      onClose={() => {}}
    />,
    { wrapper }
  )
}

describe('nodeTypeSections registry (#392 Phase 2)', () => {
  it('composes code sections: schema/code/execution present, approval config absent', () => {
    const names = NODE_TYPE_SECTIONS.code.sections.map((s) => s.name)
    expect(names).toContain('CodeSection')
    expect(names).toContain('ExecutionSection')
    expect(names).not.toContain('ApprovalConfigSection')
  })

  it('composes agent sections without the code-pool section', () => {
    const names = NODE_TYPE_SECTIONS.agent.sections.map((s) => s.name)
    expect(names).toContain('ExecutionSection')
    expect(names).not.toContain('CodeSection')
    expect(names).not.toContain('ConfigSchemaSection')
    expect(names).not.toContain('ApprovalConfigSection')
  })

  it('composes approval sections without execution/code/schema/config', () => {
    const names = NODE_TYPE_SECTIONS.approval.sections.map((s) => s.name)
    expect(names).toContain('ApprovalConfigSection')
    expect(names).toContain('DataContractSection')
    expect(names).toContain('DependencySection')
    for (const absent of [
      'ExecutionSection',
      'CodeSection',
      'ConfigSchemaSection',
      'NodeConfigSection',
    ]) {
      expect(names).not.toContain(absent)
    }
  })
})

describe('WorkflowNodeInspector for approval nodes (#392 Phase 2)', () => {
  it('renders the approval config section and no execution/code sections', async () => {
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
    renderInspector('gate')

    // 审批门配置段：rework_target 下拉 + feedback_artifact 输入。
    expect(await screen.findByLabelText('重置目标')).toHaveValue('intake')
    expect(screen.getByLabelText('评审备注文件名')).toHaveValue('review.json')
    // 执行能力 / 节点代码 / 配置 Schema 段不渲染（registry 保证）。
    expect(screen.queryByLabelText('节点执行能力')).not.toBeInTheDocument()
    expect(screen.queryByText('节点代码')).not.toBeInTheDocument()
    expect(screen.queryByText('配置 Schema')).not.toBeInTheDocument()
    // 基本设置无「能力 Key」（approval 契约禁 capability）。
    expect(screen.queryByLabelText('能力')).not.toBeInTheDocument()
    expect(screen.getByLabelText('节点名称')).toHaveValue('人工把关')
  })

  it('offers only ancestor nodes as rework candidates (execute_rework mirror)', async () => {
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
    // gate 的上游 = intake；publish 是 gate 的下游、side 是平行分支，
    // 都不是合法 rework 目标（运行期 ancestor_closure 会拒）。
    const yamlWithBranches = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake:',
      '    type: code',
      '    capability: intake',
      '    after: [_start]',
      '  side:',
      '    type: code',
      '    capability: side',
      '    after: [_start]',
      '  gate:',
      '    type: approval',
      '    after: [intake]',
      '  publish:',
      '    type: code',
      '    capability: publish',
      '    after: [gate]',
      '',
    ].join('\n')
    render(
      <WorkflowNodeInspector
        workflow={null}
        agentCatalog={[]}
        agentBindingStatus="ready"
        selectedNodeKey="gate"
        definitionYaml={yamlWithBranches}
        setDefinitionYaml={() => {}}
        onClose={() => {}}
      />,
      { wrapper }
    )

    const select = (await screen.findByLabelText(
      '重置目标'
    )) as HTMLSelectElement
    const options = Array.from(select.options).map((o) => o.value)
    expect(options).toEqual(['', 'intake'])
  })

  it('writes rework_target through the approval config patcher', async () => {
    const setDefinitionYaml = vi.fn()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
    render(
      <WorkflowNodeInspector
        workflow={null}
        agentCatalog={[]}
        agentBindingStatus="ready"
        selectedNodeKey="gate"
        definitionYaml={dagYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    fireEvent.change(await screen.findByLabelText('重置目标'), {
      target: { value: '' },
    })

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    const node = (
      yaml.load(nextYaml) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.gate
    // 清空 rework_target：白名单键从 config 删除，feedback_artifact 保留。
    expect(node?.config).toEqual({ feedback_artifact: 'review.json' })
  })

  it('does not crash on mid-edit invalid yaml (read-side defense)', async () => {
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
    // 真实路径：已发布 workflow 提供节点详情（baseline 回落），YAML 编辑器
    // 逐键落草稿产生瞬态非法文本——ApprovalConfigSection 拿 baseline 节点
    // 对非法草稿调 parse，读函数必须吞错返回默认值（subagent P1 on #399），
    // 否则整个 Studio 崩到全局错误页。
    const brokenYaml = dagYaml.replace(
      'config: {rework_target: intake, feedback_artifact: review.json}',
      'config: {rework_target: intake'
    )
    const publishedWorkflow = {
      key: 'demo',
      label: 'demo',
      nodes: [
        {
          key: 'gate',
          label: '人工把关',
          capability: '',
          after: [],
          inputs: [],
          outputs: [],
          node_type: 'approval',
        },
      ],
      edges: [],
    }
    render(
      <WorkflowNodeInspector
        workflow={publishedWorkflow as never}
        agentCatalog={[]}
        agentBindingStatus="ready"
        selectedNodeKey="gate"
        definitionYaml={brokenYaml}
        setDefinitionYaml={() => {}}
        onClose={() => {}}
      />,
      { wrapper }
    )

    // section 渲染默认值（不抛、不崩）；下拉为空候选。
    expect(await screen.findByLabelText('重置目标')).toHaveValue('')
    expect(screen.getByLabelText('评审备注文件名')).toHaveValue(
      'review_feedback.json'
    )
  })
})
