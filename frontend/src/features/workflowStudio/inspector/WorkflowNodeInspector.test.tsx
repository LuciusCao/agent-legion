import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import yaml from 'js-yaml'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { api } from '../../../api'
import { useSettingStore } from '../../../stores/settingStore'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('../../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}

const draftYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '    after: [_start]',
  'edges:',
  '  - from: _start',
  '    to: intake',
  '',
].join('\n')

// #426 codex 终轮 P2：settle 信号基线（两份查询均 settle）——本套件聚焦
// ghost 节点解析，门控组合逻辑由 agentBindingStatus.test.tsx 覆盖。
const settledSettle = {
  catalogSettled: true,
  catalogFailed: false,
  definitionsSettled: true,
  definitionsFailed: false,
}

function renderInspector(
  selectedNodeKey: string | null,
  options?: {
    definitionYaml?: string
    compareSummary?: ChangeSummaryViewModel | null
  }
) {
  return render(
    <WorkflowNodeInspector
      workflow={null}
      agentCatalog={[]}
      agentCatalogSettle={settledSettle}
      selectedNodeKey={selectedNodeKey}
      definitionYaml={options?.definitionYaml ?? draftYaml}
      setDefinitionYaml={() => {}}
      compareSummary={options?.compareSummary}
      onClose={() => {}}
    />,
    { wrapper }
  )
}

describe('WorkflowNodeInspector for draft-only (ghost) nodes', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders the editable sections for a ghost executable node', async () => {
    renderInspector('intake')

    // 基本设置（YAML 结构化编辑）可用。
    expect(await screen.findByLabelText('节点名称')).toHaveValue('读取知识点')
    expect(screen.getByLabelText('能力')).toHaveValue('intake')
    // 执行能力 / 数据契约 / 依赖段齐全。
    expect(screen.getByLabelText('节点执行能力')).toBeInTheDocument()
    expect(screen.getByText('数据契约')).toBeInTheDocument()
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
    // 不再是「选择一个节点」空态。
    expect(screen.queryByText('选择一个节点')).not.toBeInTheDocument()
  })

  it('shows the node type selector on the header (#392)', async () => {
    renderInspector('intake')

    // code 节点头部有类型选择器（原生 select），三选一；start 不在选项里。
    const selector = await screen.findByLabelText('节点类型')
    expect(selector).toHaveValue('code')
    expect(screen.getByRole('option', { name: 'Code' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Agent' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '审批门' })).toBeInTheDocument()
    expect(
      screen.queryByRole('option', { name: 'start' })
    ).not.toBeInTheDocument()
  })

  it('switches the node type via the selector and sanitizes fields', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    // intake 有可执行上游 draft_gen（approval 入边前置校验要求）。
    const yamlWithAgentFields = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  draft_gen:',
      '    type: code',
      '    label: 起草',
      '    capability: draft_gen',
      '    after: [_start]',
      '  intake:',
      '    type: agent',
      '    label: 读取知识点',
      '    capability: intake',
      '    skill: demo/skill',
      '    after: [draft_gen]',
      'edges:',
      '  - from: _start',
      '    to: draft_gen',
      '',
    ].join('\n')
    render(
      <WorkflowNodeInspector
        workflow={null}
        agentCatalog={[]}
        agentCatalogSettle={settledSettle}
        selectedNodeKey="intake"
        definitionYaml={yamlWithAgentFields}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    // 切 approval 是破坏性清洗：先确认（取消路径见下个用例）。
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('切换为审批门将清除')
    )
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    // 节点级断言（js-yaml 解析，避免 toContain 被其他节点的 type 行命中）。
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    const node = (
      yaml.load(nextYaml) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.intake
    expect(node?.type).toBe('approval')
    // capability/skill 按目标类型清洗剥除。
    expect(node).not.toHaveProperty('capability')
    expect(node).not.toHaveProperty('skill')
  })

  it('keeps the draft untouched when the approval-switch confirm is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeInspector
        workflow={null}
        agentCatalog={[]}
        agentCatalogSettle={settledSettle}
        selectedNodeKey="intake"
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    expect(setDefinitionYaml).not.toHaveBeenCalled()
  })

  it('blocks →approval without an executable upstream and toasts the reason', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    // intake 只有 start 上游：validate_approval_edges 同语义，前置校验拦下。
    render(
      <WorkflowNodeInspector
        workflow={null}
        agentCatalog={[]}
        agentCatalogSettle={settledSettle}
        selectedNodeKey="intake"
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    expect(setDefinitionYaml).not.toHaveBeenCalled()
    // 校验先于确认：前置校验都没过，就不该弹破坏性确认（P3 review）。
    expect(confirmSpy).not.toHaveBeenCalled()
    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('可执行节点的入边')
    )
  })

  it('renders no agent editor on a code node (#392 regression)', async () => {
    renderInspector('intake')

    await screen.findByLabelText('节点执行能力')
    // code 节点不再长出 Agent 编辑区（类型变更走头部选择器；#409 起该
    // 编辑区在 agent 节点内内联展开，无开合按钮）。
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
  })

  it('keeps Agent schema ownership inside Agent config (#406)', async () => {
    const agentYaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake:',
      '    type: agent',
      '    label: 读取知识点',
      '    capability: intake',
      '    after: [_start]',
      '    config_schema:',
      '      properties:',
      '        ignored_node_schema:',
      '          type: boolean',
      '',
    ].join('\n')

    renderInspector('intake', { definitionYaml: agentYaml })

    expect(await screen.findByLabelText('节点执行能力')).toHaveTextContent(
      'Agent 配置'
    )
    expect(
      screen.queryByLabelText('配置 Schema intake')
    ).not.toBeInTheDocument()
  })

  it('keeps the read-only entry contract for a ghost start node', () => {
    renderInspector('_start')

    expect(screen.getByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByLabelText('节点名称')).not.toBeInTheDocument()
  })

  it('falls back for keys absent from both baseline and draft', () => {
    // workflow 为 null（空态）且草稿里也没有：保持「未加载 workflow」语义。
    renderInspector('no_such_node')

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })
})

// 草稿 YAML 没有 start 节点：后端 loader 注入的合成 _start 不在 YAML 文本里，
// compare 把它画成 added ghost——inspector 走 compareSummary 兜底。
const yamlWithoutStart = [
  'key: demo',
  'nodes:',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '',
].join('\n')

function makeCompareSummary(
  nodeType: 'start' | 'code'
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [
      {
        type: 'added',
        nodeKey: '_start',
        label: 'Start',
        nodeType,
        fields: [],
        severity: 'info',
      },
    ],
    edgeChanges: [
      {
        type: 'added',
        source: '_start',
        target: 'intake',
        beforeCondition: null,
        afterCondition: null,
        severity: 'info',
      },
    ],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(['_start', 'intake']),
  }
}

describe('WorkflowNodeInspector compare fallback for a synthetic start ghost', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders the editable entry contract from the compare summary', async () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByText('未加载 workflow')).not.toBeInTheDocument()
    // 默认契约 material/ref（与 acceptedItemTypes fallback 语义一致）。
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /整个文件夹/ })
    ).not.toBeChecked()
    // added 边从 edgeChanges 还原进依赖关系段。
    expect(screen.getByText('0 入 / 1 出')).toBeInTheDocument()
  })

  it('does not synthesize details for a non-start node change', () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('code'),
    })

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })

  it('keeps the draft YAML resolution ahead of the compare fallback', async () => {
    // draftYaml 里有 _start（无 accepted_item_types）：YAML 路径优先，
    // compare 兜底不得覆盖它（否则三个 checkbox 会按默认契约被勾上）。
    renderInspector('_start', {
      definitionYaml: draftYaml,
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).not.toBeChecked()
  })
})
