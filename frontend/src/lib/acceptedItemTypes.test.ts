import { describe, expect, it } from 'vitest'
import {
  acceptedItemTypes,
  ITEM_TYPE_DISPLAY,
  itemTypeLabel,
  itemTypeLabels,
  type AcceptedItemType,
} from './acceptedItemTypes'
import type { WorkflowDefinitionRecord } from '../types'

function workflowWithStart(types?: string[]): WorkflowDefinitionRecord {
  return {
    nodes: [
      {
        key: '_start',
        node_type: 'start',
        accepted_item_types: types,
      },
    ],
  } as unknown as WorkflowDefinitionRecord
}

describe('acceptedItemTypes', () => {
  it('falls back to the DEFAULT contract when the definition is missing', () => {
    expect(acceptedItemTypes(null)).toEqual(['material', 'ref'])
    expect(acceptedItemTypes(undefined)).toEqual(['material', 'ref'])
    expect(acceptedItemTypes({ nodes: [] } as never)).toEqual([
      'material',
      'ref',
    ])
    expect(acceptedItemTypes(workflowWithStart([]))).toEqual([
      'material',
      'ref',
    ])
    expect(acceptedItemTypes(workflowWithStart(undefined))).toEqual([
      'material',
      'ref',
    ])
  })

  it('returns the declared start-node contract', () => {
    expect(acceptedItemTypes(workflowWithStart(['bundle']))).toEqual(['bundle'])
    expect(acceptedItemTypes(workflowWithStart(['material', 'ref']))).toEqual([
      'material',
      'ref',
    ])
  })
})

describe('ITEM_TYPE_DISPLAY', () => {
  it('covers every item type in canonical material/ref/bundle order', () => {
    // key 顺序即规范写回顺序（编辑器经 Object.keys 派生）。
    expect(Object.keys(ITEM_TYPE_DISPLAY)).toEqual([
      'material',
      'ref',
      'bundle',
    ])
    for (const type of Object.keys(ITEM_TYPE_DISPLAY)) {
      const display = ITEM_TYPE_DISPLAY[type as AcceptedItemType]
      expect(display.label).not.toBe('')
      expect(display.description).not.toBe('')
    }
  })
})

describe('itemTypeLabel', () => {
  it('returns the user-facing label for known types', () => {
    expect(itemTypeLabel('material')).toBe('上传文件')
    expect(itemTypeLabel('ref')).toBe('外部平台内容')
    expect(itemTypeLabel('bundle')).toBe('整个文件夹')
  })

  it('falls back to the raw value for unknown types', () => {
    expect(itemTypeLabel('future_type')).toBe('future_type')
  })
})

describe('itemTypeLabels', () => {
  it('joins labels in the given order', () => {
    expect(itemTypeLabels(['ref', 'material'])).toBe('外部平台内容、上传文件')
  })

  it('keeps unknown types as raw values', () => {
    expect(itemTypeLabels(['material', 'future_type'])).toBe(
      '上传文件、future_type'
    )
  })

  it('returns the placeholder for an empty contract', () => {
    expect(itemTypeLabels([])).toBe('（未声明）')
  })
})
