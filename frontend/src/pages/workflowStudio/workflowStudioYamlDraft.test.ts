import { describe, expect, it } from 'vitest'
import {
  patchWorkflowNodeInputs,
  patchWorkflowNodeLabel,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from './workflowStudioYamlDraft'

const yaml = `key: demo
label: Demo
schema_version: 2
intake:
  modes: {}
nodes:
  fetch:
    label: Fetch
    capability: fetch_questions
    after: []
    inputs: []
    outputs:
      - questions.json
  done:
    label: Done
    capability: assemble
    after:
      - fetch
    inputs:
      - questions.json
    outputs:
      - manifest.json
    terminal:
      outcome: uploadable
`

describe('workflowStudioYamlDraft node patches', () => {
  it('patches a node label', () => {
    expect(patchWorkflowNodeLabel(yaml, 'fetch', 'Fetch Questions')).toContain(
      'label: Fetch Questions'
    )
  })

  it('patches node inputs and outputs', () => {
    const withInputs = patchWorkflowNodeInputs(yaml, 'done', [
      'questions.json',
      'review.json',
    ])
    expect(withInputs).toContain('review.json')

    const withOutputs = patchWorkflowNodeOutputs(withInputs, 'done', [
      'manifest.json',
      'summary.json',
    ])
    expect(withOutputs).toContain('summary.json')
  })

  it('patches terminal outcome and can clear terminal', () => {
    const changed = patchWorkflowNodeTerminalOutcome(yaml, 'done', 'archived')
    expect(changed).toContain('outcome: archived')

    const cleared = patchWorkflowNodeTerminalOutcome(changed, 'done', '')
    expect(cleared).not.toContain('terminal:')
  })
})
