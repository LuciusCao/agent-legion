import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodePreview } from './WorkflowNodePreview'

vi.mock('../../../api/agentCatalogApi', () => ({
  getSkillDetail: vi.fn(),
}))

const mockGetSkillDetail = vi.mocked(getSkillDetail)

const node: WorkflowNodeRecord = {
  key: 'n1',
  label: '节点一',
  capability: 'cap',
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
}

const agentCatalog: AgentDefinition[] = [
  {
    id: 'agent-a',
    runtime: 'pi',
    capability: 'cap',
    skill: 'demo/agent-skill',
    tools: ['read'],
    requires_labels: {},
  } as AgentDefinition,
]

function renderPreview(options?: {
  node?: WorkflowNodeRecord
  definitionYaml?: string
}) {
  return render(
    <TestQueryProvider>
      <WorkflowNodePreview
        kind="skill"
        node={options?.node ?? node}
        agentCatalog={agentCatalog}
        definitionYaml={
          options?.definitionYaml ?? 'nodes:\n  n1:\n    capability: cap\n'
        }
        setDefinitionYaml={() => {}}
        readOnly={false}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodePreview skill key resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSkillDetail.mockResolvedValue({
      key: 'unused',
      ref: 'v1',
      commit: 'abc',
      available: true,
      files: [],
    })
  })

  it('prefers the node-declared skill from the draft yaml over the agent skill', async () => {
    renderPreview({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/node-skill\n',
    })

    expect(await screen.findByText('demo/node-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith(
      'demo/node-skill',
      undefined
    )
  })

  it('passes the mapping-form ref to the skill detail query (#76 preview pin)', async () => {
    renderPreview({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/node-skill\n      ref: v9\n',
    })

    expect(await screen.findByText('demo/node-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith('demo/node-skill', 'v9')
  })

  it('echoes the published node skill when the draft has no such node', async () => {
    renderPreview({
      node: { ...node, skill: { key: 'demo/published-skill', ref: 'v7' } },
      definitionYaml: 'nodes:\n  other_node:\n    capability: cap\n',
    })

    expect(await screen.findByText('demo/published-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith(
      'demo/published-skill',
      'v7'
    )
  })

  it('treats a draft node without a skill key as cleared (no published echo)', async () => {
    renderPreview({
      node: { ...node, skill: { key: 'demo/published-skill', ref: 'v7' } },
      // 默认草稿含 n1 但无 skill key：显式清除，回落 Agent 兜底而非
      // published 绑定（codex P2 on PR 317）。
    })

    expect(await screen.findByText('demo/agent-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).not.toHaveBeenCalledWith(
      'demo/published-skill',
      expect.anything()
    )
  })

  it('falls back to the capability-bound agent skill as the last resort', async () => {
    renderPreview()

    expect(await screen.findByText('demo/agent-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith(
      'demo/agent-skill',
      undefined
    )
  })
})
