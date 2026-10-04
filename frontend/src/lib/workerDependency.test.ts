import { describe, expect, it } from 'vitest'
import { needsAnyWorker, workflowWorkerNeeds } from './workerDependency'

function workflow(...nodeTypes: string[]) {
  return {
    nodes: nodeTypes.map((node_type, index) => ({
      key: `n${index}`,
      node_type,
    })),
  } as Parameters<typeof workflowWorkerNeeds>[0]
}

describe('workflowWorkerNeeds', () => {
  it('always needs an agent Worker for agent nodes', () => {
    for (const codeRequiresWorker of [true, false, undefined]) {
      expect(
        workflowWorkerNeeds(workflow('start', 'agent'), codeRequiresWorker)
      ).toEqual({ agent: true, code: false })
    }
  })

  it('runs code-only workflows on the Host by default', () => {
    const needs = workflowWorkerNeeds(workflow('start', 'code'), false)
    expect(needs).toEqual({ agent: false, code: false })
    expect(needsAnyWorker(needs)).toBe(false)
  })

  it('needs a code Worker on a pure-remote instance (#875)', () => {
    const needs = workflowWorkerNeeds(workflow('start', 'code', 'agent'), true)
    expect(needs).toEqual({ agent: true, code: true })
    expect(needsAnyWorker(needs)).toBe(true)
  })

  it('keeps the Host-local behavior while the deployment fact is unknown', () => {
    expect(workflowWorkerNeeds(workflow('code'), undefined).code).toBe(false)
  })

  it('never counts start or approval nodes', () => {
    expect(
      needsAnyWorker(workflowWorkerNeeds(workflow('start', 'approval'), true))
    ).toBe(false)
  })
})
