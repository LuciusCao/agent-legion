import { describe, expect, it } from 'vitest'
import { MAX_SIGNATURES, summarizeNodeCode } from './nodeCodeSummary'

describe('summarizeNodeCode（#770 窄栏代码摘要）', () => {
  it('空代码：0 行、无签名', () => {
    expect(summarizeNodeCode('')).toEqual({
      lineCount: 0,
      signatures: [],
      hiddenCount: 0,
      entrypoint: null,
    })
  })

  it('统计行数（尾随换行不计空行）并只收顶层定义，去掉行尾冒号', () => {
    const code = [
      'import os',
      '',
      'def helper(x):',
      '    def inner():',
      '        return 1',
      '    return x',
      '',
      'class Thing:  # note',
      '    pass',
      '',
    ].join('\n')
    const summary = summarizeNodeCode(code)
    expect(summary.lineCount).toBe(9)
    expect(summary.signatures).toEqual(['def helper(x)', 'class Thing'])
    expect(summary.entrypoint).toBeNull()
  })

  it('@entrypoint 装饰的函数排最前并标记为入口（含 async def）', () => {
    const code = [
      'def _util():',
      '    pass',
      '',
      '@entrypoint',
      'async def run(ctx: NodeContext) -> None:',
      '    ctx.checkpoint()',
    ].join('\n')
    const summary = summarizeNodeCode(code)
    expect(summary.entrypoint).toBe('async def run(ctx: NodeContext) -> None')
    expect(summary.signatures[0]).toBe(summary.entrypoint)
    expect(summary.signatures).toContain('def _util()')
  })

  it(`超过 ${MAX_SIGNATURES} 个顶层定义时折叠为「另有 N 个」`, () => {
    const code = ['a', 'b', 'c', 'd', 'e']
      .map((name) => `def ${name}():\n    pass`)
      .join('\n')
    const summary = summarizeNodeCode(code)
    expect(summary.signatures).toHaveLength(MAX_SIGNATURES)
    expect(summary.hiddenCount).toBe(2)
  })
})
