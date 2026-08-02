import { describe, expect, it } from 'vitest'

import {
  getInteractionQuestion,
  parseResourceInputs,
  parseResourceIds,
} from './parsers'

describe('getInteractionQuestion', () => {
  it('supports nested question payloads', () => {
    const node = { question: { instruction: '暂停思考' }, instruction: '顶层' }

    expect(getInteractionQuestion(node)).toEqual({ instruction: '暂停思考' })
  })

  it('falls back to top-level interaction fields', () => {
    const node = { instruction: '暂停思考', hint: '提示' }

    expect(getInteractionQuestion(node)).toEqual(node)
  })
})

describe('parseResourceIds', () => {
  it('splits ids by newlines and comma variants while trimming empty entries', () => {
    expect(parseResourceIds(' K001, K002\n\nQ001，Q002 ')).toEqual([
      'K001',
      'K002',
      'Q001',
      'Q002',
    ])
  })
})

describe('parseResourceInputs', () => {
  it('keeps comma-separated batch ids as separate resources', () => {
    expect(parseResourceInputs(' K001, K002\n\nQ001，Q002 ')).toEqual([
      { external_id: 'K001', source_uuid: '' },
      { external_id: 'K002', source_uuid: '' },
      { external_id: 'Q001', source_uuid: '' },
      { external_id: 'Q002', source_uuid: '' },
    ])
  })

  it('parses one external id and source uuid pair per line', () => {
    expect(parseResourceInputs('K001,uuid-1\nK002,uuid-2')).toEqual([
      { external_id: 'K001', source_uuid: 'uuid-1' },
      { external_id: 'K002', source_uuid: 'uuid-2' },
    ])
  })

  it('parses full-width comma external id and source uuid pairs', () => {
    expect(parseResourceInputs('K001，uuid-1')).toEqual([
      { external_id: 'K001', source_uuid: 'uuid-1' },
    ])
  })
})
