import { render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import {
  fetchAgentDefinition,
  fetchAgentDefinitions,
} from '../../../api/agentDefinitions'
import type {
  AgentDetailResponse,
  AgentListResponse,
  AgentVersion,
  WorkflowNodeRecord,
} from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeAgentSchemaSection } from './WorkflowNodeAgentSchemaSection'

// agent 节点的生效 schema 来自 Agent 定义详情（#406）：列表查询解析绑定，
// 详情查询取 config_schema——两者都 mock，不发真实请求。
vi.mock('../../../api/agentDefinitions', () => ({
  fetchAgentDefinitions: vi.fn(),
  fetchAgentDefinition: vi.fn(),
}))

const mockList = vi.mocked(fetchAgentDefinitions)
const mockDetail = vi.mocked(fetchAgentDefinition)

const node: WorkflowNodeRecord = {
  key: 'generate',
  label: 'Generate',
  capability: 'generate_questions',
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
}

const publishedAgent: AgentDefinition = {
  id: 'agent-generate',
  runtime: 'velites',
  capability: 'generate_questions',
  skill: 'demo/generate',
  tools: ['read'],
  requires_labels: {},
}

const agentSchema = {
  type: 'object',
  properties: {
    dry_run: { type: 'boolean', default: false, runtime_mutable: true },
    bank_version: {
      type: 'string',
      default: 'v1',
      description: '题库版本',
    },
  },
}

function version(
  definition: Record<string, unknown>,
  status: 'draft' | 'published'
): AgentVersion {
  return {
    agent_id: 'agent-generate',
    created_at: '2026-09-01T00:00:00Z',
    created_by: 'tester',
    definition,
    definition_hash: 'hash',
    id: `version-${status}`,
    published_at: status === 'published' ? '2026-09-01T00:00:00Z' : null,
    status,
    version: 1,
  }
}

// #426 codex 终轮 P2：settle 信号基线（两份查询均 settle）；在途/失败
// 场景各用例自行覆盖。
const settledSettle = {
  catalogSettled: true,
  catalogFailed: false,
  definitionsSettled: true,
  definitionsFailed: false,
}

function renderSection(
  overrides?: Partial<{
    agentCatalog: AgentDefinition[]
    agentCatalogSettle: typeof settledSettle
    readOnly: boolean
  }>
) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={
            <WorkflowNodeAgentSchemaSection
              details={{ node, incoming: [], outgoing: [] }}
              agentCatalog={overrides?.agentCatalog ?? [publishedAgent]}
              agentCatalogSettle={
                overrides?.agentCatalogSettle ?? settledSettle
              }
              readOnly={overrides?.readOnly}
            />
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowNodeAgentSchemaSection (#406 agent 生效 schema)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ agents: [] } satisfies AgentListResponse)
    mockDetail.mockResolvedValue({
      agent_id: 'agent-generate',
      published: version(
        {
          capability: 'generate_questions',
          runtime: 'velites',
          skill: 'demo/generate',
          config_schema: agentSchema,
        },
        'published'
      ),
      latest: null,
    } satisfies AgentDetailResponse)
  })

  it('renders the published agent definition schema read-only with the edit pointer', async () => {
    renderSection()

    expect(
      await screen.findByText('dry_run · boolean · 默认 false · 运行开关')
    ).toBeInTheDocument()
    expect(
      screen.getByText('bank_version · string · 默认 v1')
    ).toBeInTheDocument()
    expect(screen.getByText('题库版本')).toBeInTheDocument()
    // 指向编辑入口：本区块只读，修改去「Agent 配置」。
    expect(
      screen.getByText(/在上方「Agent 配置」区块编辑并发布 Agent/)
    ).toBeInTheDocument()
    // 只读：不出 code 节点 schema 编辑区的可编辑控件。
    expect(screen.queryByLabelText('属性名 dry_run')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('新增属性名')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '新增' })
    ).not.toBeInTheDocument()
  })

  it('prefers the published version when a newer draft exists', async () => {
    // 详情同时含 draft 与 published：dispatch 只解析 published，生效
    // schema 必须取 published 的声明（草稿里的属性不得抢先展示）。
    mockDetail.mockResolvedValue({
      agent_id: 'agent-generate',
      published: version(
        {
          capability: 'generate_questions',
          runtime: 'velites',
          skill: 'demo/generate',
          config_schema: agentSchema,
        },
        'published'
      ),
      latest: version(
        {
          capability: 'generate_questions',
          runtime: 'velites',
          skill: 'demo/generate',
          config_schema: {
            type: 'object',
            properties: { draft_only_prop: { type: 'string' } },
          },
        },
        'draft'
      ),
    } satisfies AgentDetailResponse)

    renderSection()

    expect(
      await screen.findByText('dry_run · boolean · 默认 false · 运行开关')
    ).toBeInTheDocument()
    expect(screen.queryByText(/draft_only_prop/)).not.toBeInTheDocument()
    // published 命中不是 draft 回落：无「草稿内容」提示。
    expect(screen.queryByText(/草稿内容/)).not.toBeInTheDocument()
  })

  it('falls back to the draft schema and flags it as not yet effective', async () => {
    // #387：draft-only Agent 无 published——展示草稿 schema 并明示未生效。
    mockList.mockResolvedValue({
      agents: [
        {
          agent_id: 'agent-generate',
          capability: 'generate_questions',
          has_draft: true,
          published_at: null,
          runtime: 'velites',
          skill: 'demo/generate',
          status: 'draft',
          version: 1,
        },
      ],
    } satisfies AgentListResponse)
    mockDetail.mockResolvedValue({
      agent_id: 'agent-generate',
      published: null,
      latest: version(
        {
          capability: 'generate_questions',
          runtime: 'velites',
          skill: 'demo/generate',
          config_schema: agentSchema,
        },
        'draft'
      ),
    } satisfies AgentDetailResponse)

    renderSection({ agentCatalog: [] })

    expect(
      await screen.findByText('dry_run · boolean · 默认 false · 运行开关')
    ).toBeInTheDocument()
    expect(screen.getByText(/当前展示的是草稿内容/)).toBeInTheDocument()
  })

  it('degrades gracefully when no agent is bound', async () => {
    renderSection({ agentCatalog: [] })

    expect(
      await screen.findByText('该 capability 尚无 Agent，暂无生效配置参数。')
    ).toBeInTheDocument()
    // 区块仍在（aria-label 可寻址），不是整段消失。
    expect(screen.getByLabelText('配置 Schema generate')).toBeInTheDocument()
  })

  it('keeps the section in read-only view mode without the edit pointer', async () => {
    renderSection({ readOnly: true })

    expect(
      await screen.findByText('dry_run · boolean · 默认 false · 运行开关')
    ).toBeInTheDocument()
    // 历史版本查看没有内嵌编辑器可跳：不给「上方编辑」指引。
    expect(screen.queryByText(/「Agent 配置」区块编辑/)).not.toBeInTheDocument()
  })

  it('shows the binding placeholder while the catalog is still settling', () => {
    renderSection({
      agentCatalog: [],
      agentCatalogSettle: {
        catalogSettled: false,
        catalogFailed: false,
        definitionsSettled: false,
        definitionsFailed: false,
      },
    })

    expect(screen.getByText('生效配置参数解析中...')).toBeInTheDocument()
    // 未 settle 不发详情请求（settle 后 published 可能替换 draft 回落）。
    expect(mockDetail).not.toHaveBeenCalled()
  })

  it('shows a load-error message when the definition detail fails', async () => {
    mockDetail.mockRejectedValue(new Error('boom'))

    renderSection()

    expect(
      await screen.findByText('Agent 定义加载失败，暂无法展示配置参数。')
    ).toBeInTheDocument()
  })
})
