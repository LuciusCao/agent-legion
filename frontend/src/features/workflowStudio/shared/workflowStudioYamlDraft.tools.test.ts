/** #443/#476：节点级 tools 声明的 YAML patch 语义（空即未声明）。 */

import { describe, expect, it } from 'vitest'
import { patchWorkflowNodeTools } from './workflowStudioYamlDraft'

const baseYaml = `
schema_version: 2
nodes:
  gen:
    type: agent
    capability: gen
    outputs:
      - out/a.md
`

describe('patchWorkflowNodeTools', () => {
  it('writes the declared tools list', () => {
    const next = patchWorkflowNodeTools(baseYaml, 'gen', ['read', 'uuid'])
    expect(next).toContain('tools:')
    expect(next).toContain('- read')
    expect(next).toContain('- uuid')
  })

  it('deletes the key entirely for an empty declaration (dispatch falls back)', () => {
    const declared = patchWorkflowNodeTools(baseYaml, 'gen', ['read'])
    expect(declared).toContain('tools:')
    const cleared = patchWorkflowNodeTools(declared, 'gen', [])
    expect(cleared).not.toContain('tools:')
    // 其余字段不受影响。
    expect(cleared).toContain('capability: gen')
  })

  it('leaves sibling nodes untouched', () => {
    const twoNodes = `${baseYaml}
  other:
    type: agent
    capability: other
    tools:
      - bash
`
    const next = patchWorkflowNodeTools(twoNodes, 'gen', ['write'])
    expect(next).toContain('- write')
    expect(next).toContain('- bash')
  })

  it('throws for an unknown node key', () => {
    expect(() => patchWorkflowNodeTools(baseYaml, 'missing', ['read'])).toThrow(
      'Node missing not found'
    )
  })
})
