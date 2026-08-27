import { describe, it, expect } from 'vitest'
import {
  buildOnboardingSteps,
  shouldShowEmptyGuide,
} from './onboardingReadiness'
import type { WorkflowDefinitionRecord } from '../types'
import type { WorkspaceAgentRouteEntry } from '../hooks/useWorkspaceSettingsQuery'

function makeDefinition(
  nodes: Array<{
    key: string
    execution?: { provider?: string; model?: string }
  }>
): WorkflowDefinitionRecord {
  return {
    key: 'wf',
    label: 'WF',
    intake: { modes: [] },
    edges: [],
    nodes: nodes.map((node) => ({
      key: node.key,
      label: node.key,
      after: [],
      inputs: [],
      outputs: [],
      execution: node.execution
        ? {
            provider: '',
            model: '',
            thinking: '',
            prompt: '',
            ...node.execution,
          }
        : undefined,
    })),
  } as unknown as WorkflowDefinitionRecord
}

function makeRoute(nodeKey: string): WorkspaceAgentRouteEntry {
  return {
    workflow_key: 'wf',
    node_key: nodeKey,
    node_label: nodeKey,
    capability: 'cap',
    agent_id: 'agent-1',
    agent_skill: 'skill',
  } as WorkspaceAgentRouteEntry
}

function buildSteps(overrides: {
  workflowKey?: string | null
  definition?: ReturnType<typeof makeDefinition> | null
  routes?: WorkspaceAgentRouteEntry[]
  agentDefaults?: { provider: string; model: string }
  intakeModes?: string[]
}) {
  return buildOnboardingSteps({
    workflowKey:
      overrides.workflowKey === undefined
        ? 'wf'
        : (overrides.workflowKey ?? undefined),
    workflowDefinition:
      overrides.definition === undefined
        ? makeDefinition([])
        : overrides.definition,
    agentRoutes: overrides.routes ?? [],
    agentDefaults: overrides.agentDefaults,
    intakeModes: overrides.intakeModes,
    workspaceId: 'ws1',
    goStudio: () => {},
    goSettings: () => {},
    openAddItems: () => {},
  })
}

describe('shouldShowEmptyGuide', () => {
  const settled = {
    filteredJobIds: [] as string[],
    totalJobs: 0,
    jobsLoading: false,
    filtersActive: false,
    workflowKey: null,
    workflowDefinitionLoaded: true,
  }

  it('shows the guide for a settled empty workspace', () => {
    expect(shouldShowEmptyGuide(settled)).toBe(true)
  })

  it('waits for the stats query to settle', () => {
    expect(shouldShowEmptyGuide({ ...settled, workflowKey: undefined })).toBe(
      false
    )
  })

  it('waits for the active revision query to settle', () => {
    expect(
      shouldShowEmptyGuide({ ...settled, workflowDefinitionLoaded: false })
    ).toBe(false)
  })

  it('hides the guide while jobs are loading', () => {
    expect(shouldShowEmptyGuide({ ...settled, jobsLoading: true })).toBe(false)
  })

  it('hides the guide when jobs or filters exist', () => {
    expect(shouldShowEmptyGuide({ ...settled, filteredJobIds: ['j1'] })).toBe(
      false
    )
    expect(shouldShowEmptyGuide({ ...settled, totalJobs: 1 })).toBe(false)
    expect(shouldShowEmptyGuide({ ...settled, filtersActive: true })).toBe(
      false
    )
  })
})

describe('buildOnboardingSteps readiness', () => {
  it('locks everything without a published revision (key exists since v61)', () => {
    // v61：key 创建即绑定(恒非空)，未发布的信号是 active revision 缺失。
    const steps = buildSteps({ definition: null })
    expect(steps[0].completed).toBe(false)
    expect(steps[1].unlocked).toBe(false)
    expect(steps[2].unlocked).toBe(false)
  })

  it('resolves agent nodes via node execution overrides', () => {
    // workspace 默认为空，但节点 execution.* 配齐 → 就绪（解析链节点覆盖优先）。
    const steps = buildSteps({
      definition: makeDefinition([
        { key: 'agent', execution: { provider: 'openai', model: 'gpt-5' } },
      ]),
      routes: [makeRoute('agent')],
      agentDefaults: { provider: '', model: '' },
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(true)
    expect(steps[2].unlocked).toBe(true)
  })

  it('falls back to workspace defaults when the node override is absent', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'agent' }]),
      routes: [makeRoute('agent')],
      agentDefaults: { provider: 'openai', model: 'gpt-5' },
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(true)
    expect(steps[2].unlocked).toBe(true)
  })

  it('stays locked when both override and defaults are missing', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'agent' }]),
      routes: [makeRoute('agent')],
      agentDefaults: { provider: '', model: '' },
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(false)
    expect(steps[2].unlocked).toBe(false)
  })

  it('mixes node override fields with workspace default fields', () => {
    // provider 来自默认、model 来自节点：两字段都能解析即就绪。
    const steps = buildSteps({
      definition: makeDefinition([
        { key: 'agent', execution: { model: 'gpt-5' } },
      ]),
      routes: [makeRoute('agent')],
      agentDefaults: { provider: 'openai', model: '' },
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(true)
  })

  it('requires every agent node to resolve', () => {
    const steps = buildSteps({
      definition: makeDefinition([
        { key: 'a1', execution: { provider: 'openai', model: 'gpt-5' } },
        { key: 'a2' },
      ]),
      routes: [makeRoute('a1'), makeRoute('a2')],
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(false)
  })

  it('ignores routes belonging to another workflow', () => {
    const otherWorkflowRoute = { ...makeRoute('agent'), workflow_key: 'other' }
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'agent' }]),
      routes: [otherWorkflowRoute],
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(true)
  })

  it('treats non-agent nodes as ready and needs no defaults', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'code' }]),
      intakeModes: ['manual'],
    })
    expect(steps[1].completed).toBe(true)
  })

  it('locks step 3 until at least one intake mode is enabled', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'code' }]),
      intakeModes: [],
    })
    expect(steps[1].completed).toBe(false)
    expect(steps[2].unlocked).toBe(false)
  })
})
