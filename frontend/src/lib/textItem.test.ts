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

describe('text item filename validation (backend text_item_filename 同契约)', () => {
  const config = { label: '', filename: '', template: '' }

  it.each([
    ['notes.pdf', 'suffix'],
    ['../notes.md', 'path'],
    ['sub/dir.md', 'path'],
    ['.hidden.md', 'dotfile'],
    ['notes', 'suffix'],
    ['notes.', 'suffix'],
    ['a\n.md', 'control'],
    ['bad' + String.fromCharCode(0xd800) + '.md', 'lone-surrogate'],
  ])('invalid name %s blocks ready', (filename) => {
    const item = resolveTextItem('实际需求', filename, config)
    expect(item.filenameError).not.toBeNull()
    expect(item.ready).toBe(false)
  })

  it.each(['notes.MD', '笔记.TXT', 'a.b.md', 'payload.json', 'PAYLOAD.JSON'])(
    'valid name %s keeps ready (suffix lowercased like backend)',
    (filename) => {
      const item = resolveTextItem('实际需求', filename, config)
      expect(item.filenameError).toBeNull()
      expect(item.ready).toBe(true)
    }
  )

  it('blank filename falls back to the default and stays valid', () => {
    const item = resolveTextItem('实际需求', '  ', config)
    expect(item.filename).toBe('需求.md')
    expect(item.filenameError).toBeNull()
    expect(item.ready).toBe(true)
  })
})
