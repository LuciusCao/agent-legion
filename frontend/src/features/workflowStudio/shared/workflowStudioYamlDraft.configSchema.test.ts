import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import { patchWorkflowNodeConfigSchema } from './workflowStudioYamlDraft.configSchema'
import {
  SCHEMA_PROPERTY_TYPES,
  defaultValueMatchesType,
  parseSchemaDefaultValue,
  validateSchemaPropertyName,
  validateSchemaPropertyRename,
} from './workflowStudioYamlDraft.configSchema.helpers'
import {
  addWorkflowNodeSchemaProperty,
  patchWorkflowNodeSchemaProperty,
  removeWorkflowNodeSchemaProperty,
  renameWorkflowNodeSchemaProperty,
} from './workflowStudioYamlDraft.configSchema.properties'

const dagYaml = [
  'key: demo',
  'nodes:',
  '  generate:',
  '    type: code',
  '    capability: generate_questions',
  '    config_schema:',
  '      type: object',
  '      required: [bank_version]',
  '      properties:',
  '        bank_version:',
  '          type: string',
  '          default: v1',
  '        dry_run:',
  '          type: boolean',
  '          default: false',
  '',
].join('\n')

function schemaOf(raw: string): Record<string, unknown> {
  const node = ((
    yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> }
  ).nodes?.generate ?? {}) as { config_schema?: Record<string, unknown> }
  return node.config_schema ?? {}
}

describe('validateSchemaPropertyName', () => {
  it('rejects empty, whitespace-y, duplicate, and reserved names', () => {
    expect(validateSchemaPropertyName('', [])).toContain('不能为空')
    expect(validateSchemaPropertyName('  ', [])).toContain('不能为空')
    expect(validateSchemaPropertyName('a b', [])).toContain('空格')
    expect(validateSchemaPropertyName('a.b', [])).toContain('点号')
    expect(validateSchemaPropertyName('a:b', [])).toContain('冒号')
    expect(validateSchemaPropertyName('dry_run', ['dry_run'])).toContain(
      '已存在'
    )
    expect(validateSchemaPropertyName('timeout_seconds', [])).toContain(
      '平台保留执行键'
    )
    expect(validateSchemaPropertyName('sandbox_network', [])).toContain(
      '平台保留执行键'
    )
  })

  it('accepts fresh snake_case names', () => {
    expect(validateSchemaPropertyName('page_size', ['dry_run'])).toBeNull()
  })

  it('rename keeps the current name valid', () => {
    expect(validateSchemaPropertyRename('dry_run', 'dry_run', [])).toBeNull()
    expect(
      validateSchemaPropertyRename('bank_version', 'dry_run', ['bank_version'])
    ).toContain('已存在')
  })
})

describe('schema property patches', () => {
  it('patches type/description/default/runtime_mutable in one call', () => {
    const next = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'dry_run',
      {
        type: 'boolean',
        description: '试运行',
        default: true,
        runtimeMutable: true,
      }
    )
    expect(schemaOf(next)).toMatchObject({
      properties: {
        dry_run: {
          type: 'boolean',
          description: '试运行',
          default: true,
          runtime_mutable: true,
        },
      },
    })
  })

  it('drops description when patched to an empty string', () => {
    const described = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'dry_run',
      {
        description: '临时',
      }
    )
    const next = patchWorkflowNodeSchemaProperty(
      described,
      'generate',
      'dry_run',
      {
        description: '',
      }
    )
    expect(
      (schemaOf(next).properties as Record<string, unknown>).dry_run
    ).not.toHaveProperty('description')
  })

  it('drops an incompatible default when the type changes', () => {
    const next = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'bank_version',
      {
        type: 'integer',
      }
    )
    expect(schemaOf(next)).toMatchObject({
      properties: { bank_version: { type: 'integer' } },
    })
    expect(
      (schemaOf(next).properties as Record<string, Record<string, unknown>>)
        .bank_version
    ).not.toHaveProperty('default')
  })

  it('keeps a compatible default across a number→integer type change', () => {
    const withNumber = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'bank_version',
      {
        type: 'number',
        default: 1.5,
      }
    )
    const next = patchWorkflowNodeSchemaProperty(
      withNumber,
      'generate',
      'bank_version',
      {
        type: 'number',
      }
    )
    expect(schemaOf(next)).toMatchObject({
      properties: { bank_version: { type: 'number', default: 1.5 } },
    })
  })

  it('unchecking runtime_mutable deletes the key instead of writing false', () => {
    const mutable = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'dry_run',
      {
        runtimeMutable: true,
      }
    )
    const next = patchWorkflowNodeSchemaProperty(
      mutable,
      'generate',
      'dry_run',
      {
        runtimeMutable: false,
      }
    )
    expect(
      (schemaOf(next).properties as Record<string, unknown>).dry_run
    ).not.toHaveProperty('runtime_mutable')
  })

  it('throws for unknown nodes or properties', () => {
    expect(() =>
      patchWorkflowNodeSchemaProperty(dagYaml, 'missing', 'dry_run', {})
    ).toThrow('Node missing not found')
    expect(() =>
      patchWorkflowNodeSchemaProperty(dagYaml, 'generate', 'missing', {})
    ).toThrow('Property missing not found')
  })
})

describe('add/rename/remove schema properties', () => {
  it('adds a property and creates the schema skeleton when absent', () => {
    const bare = 'key: demo\nnodes:\n  generate:\n    capability: c\n'
    const next = addWorkflowNodeSchemaProperty(
      bare,
      'generate',
      'page_size',
      'integer'
    )
    expect(schemaOf(next)).toMatchObject({
      type: 'object',
      properties: { page_size: { type: 'integer' } },
    })
  })

  it('renames and rewrites required references', () => {
    const next = renameWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'bank_version',
      'bank'
    )
    expect(schemaOf(next)).toMatchObject({
      required: ['bank'],
      properties: {
        bank: { type: 'string', default: 'v1' },
        dry_run: { type: 'boolean' },
      },
    })
  })

  it('removes a property and its required reference', () => {
    const next = removeWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'bank_version'
    )
    expect(schemaOf(next)).toMatchObject({
      properties: { dry_run: { type: 'boolean' } },
    })
    expect(schemaOf(next)).not.toHaveProperty('required')
  })

  it('drops the whole config_schema when the last property goes', () => {
    const single = [
      'key: demo',
      'nodes:',
      '  generate:',
      '    capability: c',
      '    config_schema:',
      '      type: object',
      '      properties:',
      '        dry_run:',
      '          type: boolean',
      '',
    ].join('\n')
    const next = removeWorkflowNodeSchemaProperty(single, 'generate', 'dry_run')
    const node =
      (yaml.load(next) as { nodes?: Record<string, Record<string, unknown>> })
        .nodes?.generate ?? {}
    expect(node).not.toHaveProperty('config_schema')
  })
})

describe('patchWorkflowNodeConfigSchema (whole-block replace)', () => {
  it('replaces and deletes the whole schema block', () => {
    const replaced = patchWorkflowNodeConfigSchema(dagYaml, 'generate', {
      type: 'object',
      properties: { only: { type: 'string' } },
    })
    expect(schemaOf(replaced)).toMatchObject({
      properties: { only: { type: 'string' } },
    })
    const removed = patchWorkflowNodeConfigSchema(
      replaced,
      'generate',
      undefined
    )
    const node =
      (
        yaml.load(removed) as {
          nodes?: Record<string, Record<string, unknown>>
        }
      ).nodes?.generate ?? {}
    expect(node).not.toHaveProperty('config_schema')
  })
})

describe('default value coercion', () => {
  it('parses defaults per type and treats blank as unset', () => {
    expect(parseSchemaDefaultValue('', 'string')).toBeUndefined()
    expect(parseSchemaDefaultValue('  ', 'integer')).toBeUndefined()
    expect(parseSchemaDefaultValue('v2', 'string')).toBe('v2')
    expect(parseSchemaDefaultValue('42', 'integer')).toBe(42)
    expect(parseSchemaDefaultValue('1.5', 'number')).toBe(1.5)
    expect(parseSchemaDefaultValue('true', 'boolean')).toBe(true)
    expect(parseSchemaDefaultValue('abc', 'integer')).toBeUndefined()
  })

  it('checks value/type compatibility', () => {
    expect(defaultValueMatchesType('v1', 'string')).toBe(true)
    expect(defaultValueMatchesType('v1', 'integer')).toBe(false)
    expect(defaultValueMatchesType(1.5, 'number')).toBe(true)
    expect(defaultValueMatchesType(1.5, 'integer')).toBe(false)
    expect(defaultValueMatchesType(true, 'boolean')).toBe(true)
    expect(defaultValueMatchesType(1, 'boolean')).toBe(false)
  })

  it('exposes the backend type subset', () => {
    // server/app/config_schema.py _SCHEMA_TYPES 一一对应。
    expect([...SCHEMA_PROPERTY_TYPES]).toEqual([
      'string',
      'integer',
      'number',
      'boolean',
    ])
  })
})
