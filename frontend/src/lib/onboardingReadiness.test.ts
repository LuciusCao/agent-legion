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
    // 过滤键是 workspace_id（workflow_key 已 deprecated 且 v62 起恒等）。
    workflow_key: 'ws1',
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
}) {
  return buildOnboardingSteps({
    workflowKey:
      overrides.workflowKey === undefined
        ? 'ws1'
        : (overrides.workflowKey ?? undefined),
    workflowDefinition:
      overrides.definition === undefined
        ? makeDefinition([])
        : overrides.definition,
    agentRoutes: overrides.routes ?? [],
    workspaceId: 'ws1',
    goStudio: () => {},
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
  it('locks everything without a published revision (key exists since v62)', () => {
    // v62：key 创建即绑定(恒非空)，未发布的信号是 active revision 缺失。
    const steps = buildSteps({ definition: null })
    expect(steps[0].completed).toBe(false)
    expect(steps[1].unlocked).toBe(false)
    expect(steps[2].unlocked).toBe(false)
  })

  it('resolves agent nodes via their effective node execution', () => {
    // active revision 快照的节点 execution 已被 loader 合并顶层默认，
    // provider+model 齐备即就绪（workspace 默认已退役）。
    const steps = buildSteps({
      definition: makeDefinition([
        { key: 'agent', execution: { provider: 'openai', model: 'gpt-5' } },
      ]),
      routes: [makeRoute('agent')],
    })
    expect(steps[1].completed).toBe(true)
    expect(steps[2].unlocked).toBe(true)
  })

  it('stays locked when the agent node execution is incomplete', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'agent' }]),
      routes: [makeRoute('agent')],
    })
    expect(steps[1].completed).toBe(false)
    expect(steps[2].unlocked).toBe(false)
  })

  it('requires every agent node to resolve', () => {
    const steps = buildSteps({
      definition: makeDefinition([
        { key: 'a1', execution: { provider: 'openai', model: 'gpt-5' } },
        { key: 'a2' },
      ]),
      routes: [makeRoute('a1'), makeRoute('a2')],
    })
    expect(steps[1].completed).toBe(false)
  })

  it('ignores routes belonging to another workflow', () => {
    const otherWorkflowRoute = { ...makeRoute('agent'), workflow_key: 'other' }
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'agent' }]),
      routes: [otherWorkflowRoute],
    })
    expect(steps[1].completed).toBe(true)
  })

  it('treats non-agent nodes as ready', () => {
    const steps = buildSteps({
      definition: makeDefinition([{ key: 'code' }]),
    })
    expect(steps[1].completed).toBe(true)
    expect(steps[2].unlocked).toBe(true)
  })
})
