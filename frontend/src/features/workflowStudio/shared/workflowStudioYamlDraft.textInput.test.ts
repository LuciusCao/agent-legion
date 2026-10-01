import { describe, expect, it } from 'vitest'
import yaml from 'js-yaml'
import {
  normalizeTextInput,
  patchWorkflowNodeTextInput,
} from './workflowStudioYamlDraft.textInput'
import { workflowYamlToDefinitionRecord } from '../canvas/workflowYamlDraftRecord'
import { ghostDraftNodeDetails } from '../canvas/workflowStudioGhostNode'

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

describe('text_input runtime boundary', () => {
  it.each([123, [], ['invalid'], ['material', 123], { bad: 'value' }])(
    'rejects an unsafe entry contract before it reaches the section renderer %j',
    (value) => {
      const raw = yaml.dump({
        nodes: {
          _start: {
            type: 'start',
            accepted_item_types: value,
            text_input: { template: 'valid' },
          },
        },
      })
      expect(workflowYamlToDefinitionRecord(raw)).toBeNull()
      expect(ghostDraftNodeDetails(raw, '_start')).toBeNull()
    }
  )
  const invalid = [
    123,
    false,
    'template',
    [],
    new Date('2026-01-01'),
    { unexpected: 'value' },
    ...['label', 'filename', 'template'].flatMap((key) =>
      [123, false, ['text'], { nested: 'text' }].map((value) => ({
        [key]: value,
      }))
    ),
  ]
  it.each(invalid.map((value) => [value]))(
    'rejects malformed draft values %j without polluting either record path',
    (value) => {
      const raw = yaml.dump({
        nodes: {
          _start: {
            type: 'start',
            accepted_item_types: ['text'],
            text_input: value,
          },
        },
      })
      expect(normalizeTextInput(value)).toBeUndefined()
      expect(workflowYamlToDefinitionRecord(raw)).toBeNull()
      expect(ghostDraftNodeDetails(raw, '_start')).toBeNull()
      expect(() =>
        patchWorkflowNodeTextInput(raw, '_start', {
          label: 'repair',
          filename: '',
          template: '',
        })
      ).toThrow('Invalid text_input')
      expect(load(raw).nodes._start.text_input).toEqual(value)
    }
  )
  it.each([
    undefined,
    null,
    {},
    { label: null, filename: null, template: null },
    { label: ' ', filename: '\t', template: '\n' },
  ])('normalizes empty blocks %j to absent', (value) => {
    expect(normalizeTextInput(value)).toBeNull()
  })
  it('produces identical typed records for both rendering paths and preserves multiline text', () => {
    const value = {
      label: '  需求 ',
      filename: null,
      template: '# 需求\n  保留缩进\n',
    }
    const raw = yaml.dump({
      nodes: { _start: { type: 'start', text_input: value } },
    })
    const expected = { label: '需求', filename: '', template: value.template }
    expect(workflowYamlToDefinitionRecord(raw)?.nodes[0].text_input).toEqual(
      expected
    )
    expect(ghostDraftNodeDetails(raw, '_start')?.node.text_input).toEqual(
      expected
    )
  })
})

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
