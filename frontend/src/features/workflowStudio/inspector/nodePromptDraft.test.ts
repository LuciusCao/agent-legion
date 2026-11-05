import { describe, expect, it } from 'vitest'
import type { WorkflowNodeRecord } from '../../../types'
import { readNodePromptDraft } from './nodePromptDraft'

const node: WorkflowNodeRecord = {
  key: 'n1',
  label: '节点一',
  capability: 'cap',
  after: [],
  inputs: [],
  outputs: [],
}

describe('readNodePromptDraft', () => {
  it('normalizes junk prompt and mode values to an empty append draft', () => {
    const yaml = [
      'nodes:',
      '  n1:',
      '    execution:',
      '      prompt: 123',
      '      prompt_mode: [invalid]',
      '',
    ].join('\n')

    expect(readNodePromptDraft(yaml, node)).toEqual({
      customPrompt: '',
      isDefault: true,
      promptMode: 'append',
    })
  })

  it('preserves custom whitespace and overwrite mode from the draft', () => {
    const yaml = [
      'nodes:',
      '  n1:',
      '    execution:',
      '      prompt: "  自定义  "',
      '      prompt_mode: overwrite',
      '',
    ].join('\n')

    expect(readNodePromptDraft(yaml, node)).toEqual({
      customPrompt: '  自定义  ',
      isDefault: false,
      promptMode: 'overwrite',
    })
  })

  it('falls back to the published execution when the draft cannot be parsed', () => {
    const published = {
      ...node,
      execution: { prompt: '已发布', prompt_mode: 'overwrite' },
    } as WorkflowNodeRecord

    expect(readNodePromptDraft('nodes: [', published)).toEqual({
      customPrompt: '已发布',
      isDefault: false,
      promptMode: 'overwrite',
    })
  })
})
