import { describe, expect, it } from 'vitest'
import { resolveTextItem, textRunItem } from './textItem'

describe('text item filename precedence', () => {
  it.each([null, '', '   ', '自定义.txt'])(
    'uses an explicit nonblank name, then the workflow default (%s)',
    (filename) => {
      const item = resolveTextItem('实际需求', filename, {
        label: '',
        filename: '工作流需求.md',
        template: '',
      })
      expect(item.ready).toBe(true)
      expect(textRunItem(item).filename).toBe(
        filename?.trim() || '工作流需求.md'
      )
    }
  )

  it('uses the built-in default only when both names are blank', () => {
    const item = resolveTextItem('实际需求', ' ', {
      label: '',
      filename: '',
      template: '',
    })
    expect(textRunItem(item).filename).toBe('需求.md')
  })
})
