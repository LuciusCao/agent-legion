import { describe, it, expect } from 'vitest'
import {
  buildOnboardingSteps,
  shouldShowEmptyGuide,
} from './onboardingReadiness'
import type { WorkflowDefinitionRecord } from '../types'

function makeDefinition(): WorkflowDefinitionRecord {
  return {
    key: 'wf',
    label: 'WF',
    intake: { modes: [] },
    edges: [],
    nodes: [],
  } as unknown as WorkflowDefinitionRecord
}

function buildSteps(overrides: {
  definition?: ReturnType<typeof makeDefinition> | null
}) {
  return buildOnboardingSteps({
    workflowDefinition:
      overrides.definition === undefined
        ? makeDefinition()
        : overrides.definition,
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
  // #333：引导收敛为 2 步（发布 workflow → 添加第一个任务）；agent 节点
  // provider/model 缺口不再阻塞引导，改由 Studio 画布实时警报承载。
  it('is a two-step guide', () => {
    const steps = buildSteps({ definition: null })
    expect(steps.map((step) => step.title)).toEqual([
      '创建并发布 Workflow',
      '添加第一个任务',
    ])
  })

  it('locks the add-item step without a published revision (key exists since v62)', () => {
    // v62：key 创建即绑定(恒非空)，未发布的信号是 active revision 缺失。
    const steps = buildSteps({ definition: null })
    expect(steps[0].completed).toBe(false)
    expect(steps[1].unlocked).toBe(false)
  })

  it('completes step 1 and unlocks step 2 once a revision is published', () => {
    const steps = buildSteps({ definition: makeDefinition() })
    expect(steps[0].completed).toBe(true)
    expect(steps[1].unlocked).toBe(true)
  })
})
