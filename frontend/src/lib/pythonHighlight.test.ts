import { describe, expect, it } from 'vitest'
import { splitTokensByLine, tokenizePython } from './pythonHighlight'

describe('tokenizePython', () => {
  it('classifies keywords, numbers and plain identifiers', () => {
    const tokens = tokenizePython('def run(x):\n    return x + 1\n')
    const byText = new Map(
      tokens.filter((t) => t.kind !== 'plain').map((t) => [t.text, t.kind])
    )
    expect(byText.get('def')).toBe('keyword')
    expect(byText.get('return')).toBe('keyword')
    expect(byText.get('1')).toBe('number')
    expect(byText.get('run')).toBeUndefined()
    expect(byText.get('x')).toBeUndefined()
  })

  it('treats True/False/None as keywords', () => {
    const kinds = tokenizePython('x = True if None else False')
      .filter((t) => t.kind === 'keyword')
      .map((t) => t.text)
    expect(kinds).toEqual(['True', 'if', 'None', 'else', 'False'])
  })

  it('highlights strings and keeps # inside a string as string', () => {
    const tokens = tokenizePython('label = "a#b"  # real comment\n')
    expect(tokens).toContainEqual({ text: '"a#b"', kind: 'string' })
    expect(tokens).toContainEqual({ text: '# real comment', kind: 'comment' })
  })

  it('highlights triple-quoted strings across lines', () => {
    const tokens = tokenizePython('s = """line1\nline2"""\n')
    expect(tokens).toContainEqual({
      text: '"""line1\nline2"""',
      kind: 'string',
    })
  })

  it('highlights decorators and trailing comments', () => {
    const tokens = tokenizePython('@app.route\n# top comment\ndef view():\n')
    expect(tokens).toContainEqual({ text: '@app.route', kind: 'decorator' })
    expect(tokens).toContainEqual({ text: '# top comment', kind: 'comment' })
  })

  it('round-trips the original source', () => {
    const code =
      'import json\n\ndef run(job, job_dir, runtime):  # entry\n    return {"ok": 1}\n'
    expect(
      tokenizePython(code)
        .map((t) => t.text)
        .join('')
    ).toBe(code)
  })
})

describe('splitTokensByLine', () => {
  it('splits multi-line tokens across lines', () => {
    const lines = splitTokensByLine(tokenizePython('s = """a\nb"""\n'))
    expect(lines).toHaveLength(3)
    expect(lines[0].map((t) => t.text).join('')).toBe('s = """a')
    expect(lines[1]).toContainEqual({ text: 'b"""', kind: 'string' })
    expect(lines[2]).toEqual([])
  })
})
