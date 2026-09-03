import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
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
    expect(parseConfigValue('abc', { type: 'integer' })).toBeUndefined()
  })

  it('formats values back for form display', () => {
    expect(formatConfigValue(undefined)).toBe('')
    expect(formatConfigValue('v1')).toBe('v1')
    expect(formatConfigValue(20)).toBe('20')
    expect(formatConfigValue(true)).toBe('true')
  })
})
