import { describe, expect, it } from 'vitest'
import yaml from 'js-yaml'
import { patchWorkflowNodeTextInput } from './workflowStudioYamlDraft.textInput'

const baseYaml = `
schema_version: 2
nodes:
  _start:
    type: start
    accepted_item_types: [material, text]
  gen:
    type: agent
    capability: gen
`

const load = (raw: string) =>
  yaml.load(raw) as { nodes: Record<string, Record<string, unknown>> }

describe('patchWorkflowNodeTextInput', () => {
  it('writes only the non-empty keys and keeps the template verbatim', () => {
    const next = patchWorkflowNodeTextInput(baseYaml, '_start', {
      label: ' 创作需求 ',
      filename: '',
      template: '# 歌曲创作需求\n- 参考歌曲：\n',
    })
    expect(load(next).nodes._start.text_input).toEqual({
      label: '创作需求',
      template: '# 歌曲创作需求\n- 参考歌曲：\n',
    })
    expect(load(next).nodes._start.accepted_item_types).toEqual([
      'material',
      'text',
    ])
    expect(load(next).nodes.gen).toEqual({ type: 'agent', capability: 'gen' })
  })

  it('deletes the block when every field is empty', () => {
    const declared = patchWorkflowNodeTextInput(baseYaml, '_start', {
      label: '',
      filename: '需求.md',
      template: '',
    })
    expect(load(declared).nodes._start.text_input).toEqual({
      filename: '需求.md',
    })
    const cleared = patchWorkflowNodeTextInput(declared, '_start', {
      label: '  ',
      filename: '',
      template: '',
    })
    expect(cleared).not.toContain('text_input')
  })

  it('creates the start node when the synthetic _start is absent', () => {
    const next = patchWorkflowNodeTextInput(
      'nodes:\n  gen:\n    capability: gen\n',
      '_start',
      {
        label: '需求',
        filename: '',
        template: '',
      }
    )
    expect(load(next).nodes._start).toEqual({
      type: 'start',
      text_input: { label: '需求' },
    })
  })
})
