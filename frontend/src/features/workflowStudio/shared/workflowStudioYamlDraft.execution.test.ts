import { describe, expect, it } from 'vitest'
import { patchWorkflowNodeExecution } from './workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from './workflowStudioYamlDraft.parse'

describe('patchWorkflowNodeExecution', () => {
  it('drops non-string execution junk values instead of throwing (codex P1 family)', () => {
    // `provider: 1` / `model: true`：合法 YAML 非法契约值。P1 让画布容忍
    // junk 后，inspector 编辑路径成为可达修复路径——编辑其他键时非字符串
    // 值按空删除，不得让 .trim() 抛异常（reviewer-m4 r2）。
    const yaml = [
      'nodes:',
      '  a:',
      '    capability: cap',
      '    execution:',
      '      provider: 1',
      '      model: true',
      '',
    ].join('\n')
    const next = patchWorkflowNodeExecution(yaml, 'a', 'model', 'gpt-5')
    expect(parseWorkflowNode(next, 'a')?.execution).toEqual({
      model: 'gpt-5',
    })
  })

  it('removes the execution block when junk and emptied values are all that remain', () => {
    const yaml = [
      'nodes:',
      '  a:',
      '    capability: cap',
      '    execution:',
      '      provider: 1',
      '',
    ].join('\n')
    const next = patchWorkflowNodeExecution(yaml, 'a', 'provider', '')
    expect(parseWorkflowNode(next, 'a')?.execution).toBeUndefined()
  })
})
