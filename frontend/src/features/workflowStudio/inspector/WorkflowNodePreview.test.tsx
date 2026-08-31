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

  it('reads the key from the mapping form with an explicit ref', async () => {
    renderPreview({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/node-skill\n      ref: v9\n',
    })

    expect(await screen.findByText('demo/node-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith(
      'demo/node-skill',
      undefined
    )
  })

  it('falls back to the published node skill when the draft has none', async () => {
    renderPreview({
      node: { ...node, skill: { key: 'demo/published-skill', ref: '' } },
    })

    expect(await screen.findByText('demo/published-skill')).toBeInTheDocument()
    expect(mockGetSkillDetail).toHaveBeenCalledWith(
      'demo/published-skill',
      undefined
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
