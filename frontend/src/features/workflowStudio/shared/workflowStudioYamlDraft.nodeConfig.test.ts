import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  configValueCommitError,
  storedConfigValueError,
} from './workflowStudioYamlDraft.configValueValidation'
import {
  formatConfigValue,
  parseConfigValue,
  patchWorkflowNodeConfigValue,
  readNodeConfig,
  readNodeConfigSchema,
} from './workflowStudioYamlDraft.nodeConfig'

const dagYaml = [
  'key: demo',
  'nodes:',
  '  generate:',
  '    type: code',
  '    capability: generate_questions',
  '    config_schema:',
  '      type: object',
  '      properties:',
  '        page_size:',
  '          type: integer',
  '          default: 50',
  '    config:',
  '      page_size: 20',
  '',
].join('\n')

function nodeOf(raw: string): Record<string, unknown> {
  return (
    (yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> })
      .nodes?.generate ?? {}
  )
}

describe('readNodeConfig / readNodeConfigSchema', () => {
  it('reads the declared config and schema', () => {
    expect(readNodeConfig(dagYaml, 'generate')).toEqual({ page_size: 20 })
    expect(readNodeConfigSchema(dagYaml, 'generate')).toMatchObject({
      properties: { page_size: { type: 'integer', default: 50 } },
    })
  })

  it('returns empty/undefined for missing nodes or absent blocks', () => {
    expect(readNodeConfig(dagYaml, 'missing')).toEqual({})
    expect(readNodeConfigSchema(dagYaml, 'missing')).toBeUndefined()
    const bare = 'key: demo\nnodes:\n  generate:\n    capability: c\n'
    expect(readNodeConfig(bare, 'generate')).toEqual({})
    expect(readNodeConfigSchema(bare, 'generate')).toBeUndefined()
  })

  it('swallows mid-edit invalid YAML instead of throwing', () => {
    const broken = dagYaml.replace(
      '    config:\n      page_size: 20',
      '    config: {page_size: 20'
    )
    expect(readNodeConfig(broken, 'generate')).toEqual({})
    expect(readNodeConfigSchema(broken, 'generate')).toBeUndefined()
  })
})

describe('patchWorkflowNodeConfigValue', () => {
  it('writes a typed value into the node config', () => {
    const next = patchWorkflowNodeConfigValue(
      dagYaml,
      'generate',
      'page_size',
      33
    )
    expect(nodeOf(next).config).toEqual({ page_size: 33 })
  })

  it('clears the key on undefined and drops an empty config block', () => {
    const cleared = patchWorkflowNodeConfigValue(
      dagYaml,
      'generate',
      'page_size',
      undefined
    )
    expect(nodeOf(cleared)).not.toHaveProperty('config')
  })

  it('keeps sibling keys when patching one', () => {
    const two = patchWorkflowNodeConfigValue(
      dagYaml,
      'generate',
      'dry_run',
      true
    )
    expect(nodeOf(two).config).toEqual({ page_size: 20, dry_run: true })
    const next = patchWorkflowNodeConfigValue(two, 'generate', 'page_size', '')
    expect(nodeOf(next).config).toEqual({ dry_run: true })
  })

  it('throws for unknown nodes', () => {
    expect(() =>
      patchWorkflowNodeConfigValue(dagYaml, 'missing', 'k', 1)
    ).toThrow('Node missing not found')
  })
})

describe('value coercion helpers', () => {
  it('parses form strings per schema type', () => {
    expect(parseConfigValue('', { type: 'string' })).toBeUndefined()
    expect(parseConfigValue('v2', { type: 'string' })).toBe('v2')
    expect(parseConfigValue('42', { type: 'integer' })).toBe(42)
    expect(parseConfigValue('1.5', { type: 'number' })).toBe(1.5)
    expect(parseConfigValue('true', { type: 'boolean' })).toBe(true)
    // 垃圾数字输入返回 null 而非 undefined（三轮复审 P3-3，对齐默认值
    // 编辑器 NIT-2b 的 parseSchemaDefaultValue）：undefined 是显式清空
    // 的删键信号，null = 不可解析，提交路径据此行内报错不删键。
    expect(parseConfigValue('abc', { type: 'integer' })).toBeNull()
    expect(parseConfigValue('abc', { type: 'number' })).toBeNull()
  })

  it('formats values back for form display', () => {
    expect(formatConfigValue(undefined)).toBe('')
    expect(formatConfigValue('v1')).toBe('v1')
    expect(formatConfigValue(20)).toBe('20')
    expect(formatConfigValue(true)).toBe('true')
  })
})

describe('config value validation (#428 三轮 P3-3/P3-4)', () => {
  it('flags unparseable numeric commits instead of treating them as unset', () => {
    expect(configValueCommitError('abc', { type: 'integer' })).toContain(
      '无法解析为 integer'
    )
    expect(configValueCommitError('abc', { type: 'number' })).toContain(
      '无法解析为 number'
    )
    // 显式清空是合法的删键路径，不是错误。
    expect(configValueCommitError('', { type: 'integer' })).toBeNull()
    expect(configValueCommitError('  ', { type: 'number' })).toBeNull()
    // 合法值照常通过约束，非法值仍被 enum/边界拦截。
    const bounded = { type: 'integer' as const, minimum: 1, maximum: 50 }
    expect(configValueCommitError('25', bounded)).toBeNull()
    expect(configValueCommitError('99', bounded)).toContain('不得大于 50')
  })

  it('flags stored values that mismatch the property type (#428 三轮 P3-4)', () => {
    // 落盘类型值直接判定：经表单串往返会抹掉类型信息，这些值在
    // 表单里显示正常，发布后 intake 的 _type_matches 才 raise。
    expect(storedConfigValueError({ type: 'string' }, 42)).toContain(
      '存量值与类型 string 不匹配'
    )
    expect(storedConfigValueError({ type: 'number' }, '20')).toContain(
      '存量值与类型 number 不匹配'
    )
    expect(storedConfigValueError({ type: 'integer' }, 1.5)).toContain(
      '存量值与类型 integer 不匹配'
    )
    expect(storedConfigValueError({ type: 'boolean' }, 42)).toContain(
      '存量值与类型 boolean 不匹配'
    )
    // 未存值不误报；类型匹配后照常跑约束（P3-1 行为保留）。
    expect(storedConfigValueError({ type: 'integer' }, undefined)).toBeNull()
    expect(storedConfigValueError({ type: 'integer' }, 99)).toBeNull()
    expect(
      storedConfigValueError({ type: 'integer', maximum: 50 }, 99)
    ).toContain('不得大于 50')
  })
})
