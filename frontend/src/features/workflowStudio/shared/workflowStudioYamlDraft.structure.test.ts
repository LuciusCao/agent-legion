import { describe, expect, it } from 'vitest'
import { patchWorkflowNodeTools } from './workflowStudioYamlDraft'
import { parseWorkflowYaml } from './workflowStudioYamlDraft.parse'

const baseYaml = `
key: demo
nodes:
  gen:
    type: agent
    label: Generate
    capability: gen
    config:
      page_size: 20
      nested:
        enabled: true
    config_schema:
      type: object
      properties:
        page_size:
          type: integer
    tools:
      - read
  other:
    type: agent
    capability: other
    tools:
      - bash
`

describe('workflow YAML node patch structure', () => {
  it('replaces only the target tools field and preserves config and siblings', () => {
    const before = parseWorkflowYaml(baseYaml)
    const next = parseWorkflowYaml(
      patchWorkflowNodeTools(baseYaml, 'gen', ['read', 'uuid'])
    )

    expect(next.nodes?.gen).toEqual({
      ...before.nodes?.gen,
      tools: ['read', 'uuid'],
    })
    expect(next.nodes?.other).toEqual(before.nodes?.other)
  })

  it('clears tools without changing the remaining node structure', () => {
    const cleared = parseWorkflowYaml(
      patchWorkflowNodeTools(baseYaml, 'gen', [])
    )
    const expected = { ...parseWorkflowYaml(baseYaml).nodes?.gen }
    delete expected.tools

    expect(cleared.nodes?.gen).toEqual(expected)
  })
})
