import { describe, expect, it } from 'vitest'
import { workflowNeedsWorker } from './workerDependency'

function workflow(...nodeTypes: string[]) {
  return {
    nodes: nodeTypes.map((node_type, index) => ({
      key: `n${index}`,
      node_type,
    })),
  } as Parameters<typeof workflowNeedsWorker>[0]
}

describe('workflowNeedsWorker', () => {
  it('always needs a Worker for agent nodes', () => {
    for (const codeRequiresWorker of [true, false, undefined]) {
      expect(
        workflowNeedsWorker(workflow('start', 'agent'), codeRequiresWorker)
      ).toBe(true)
    }
  })

  it('runs code-only workflows on the Host by default', () => {
    expect(workflowNeedsWorker(workflow('start', 'code'), false)).toBe(false)
  })

  it('counts code nodes when the instance is pure-remote (#875)', () => {
    expect(workflowNeedsWorker(workflow('start', 'code'), true)).toBe(true)
  })

  it('keeps the Host-local behavior while the deployment fact is unknown', () => {
    expect(workflowNeedsWorker(workflow('code'), undefined)).toBe(false)
  })

  it('never counts start or approval nodes', () => {
    expect(workflowNeedsWorker(workflow('start', 'approval'), true)).toBe(false)
  })
})
