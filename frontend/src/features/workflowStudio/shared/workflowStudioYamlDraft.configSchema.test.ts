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
  configValueConstraintError,
  isSecretConfigProperty,
} from './workflowStudioYamlDraft.configSchema.constraints'
import { isConfigValueOfType } from './workflowStudioYamlDraft.configSchema.configLink'
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

// 带 node config 的变体：rename/remove 的连带迁移（#428 复审 P1）
// 断言基础。
const dagYamlWithConfig = [
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
  '    config:',
  '      bank_version: v2',
  '      dry_run: true',
  '',
].join('\n')

function schemaOf(raw: string): Record<string, unknown> {
  const node = ((
    yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> }
  ).nodes?.generate ?? {}) as { config_schema?: Record<string, unknown> }
  return node.config_schema ?? {}
}

function configOf(raw: string): Record<string, unknown> {
  const node = ((
    yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> }
  ).nodes?.generate ?? {}) as { config?: Record<string, unknown> }
  return node.config ?? {}
}

/** #428 复审 P1 通用断言：config 的键必须 ⊆ schema properties 的键。
 * 后端 intake 对 config 未知键直接 raise（validate_config_values 白名单），
 * 留孤儿键 = 发布后新 job 创建失败。 */
function expectNoOrphanConfigKeys(raw: string): void {
  const properties = (schemaOf(raw).properties ?? {}) as Record<string, unknown>
  const configKeys = Object.keys(configOf(raw))
  const orphans = configKeys.filter((key) => !(key in properties))
  expect(orphans).toEqual([])
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

  it('deletes an existing default when the patch carries default: undefined (#428 codex P2-A)', () => {
    // { default: undefined } 是显式删除，不是「未提供」。
    const next = patchWorkflowNodeSchemaProperty(
      dagYaml,
      'generate',
      'bank_version',
      { default: undefined }
    )
    expect(
      (schemaOf(next).properties as Record<string, Record<string, unknown>>)
        .bank_version
    ).not.toHaveProperty('default')
  })

  it('clears enum/minimum/maximum when the type changes (#428 codex P2-B)', () => {
    // number + minimum/maximum 改 string：numeric 约束不再可信，全清。
    const constrained = [
      'key: demo',
      'nodes:',
      '  generate:',
      '    capability: generate_questions',
      '    config_schema:',
      '      type: object',
      '      properties:',
      '        page_size:',
      '          type: number',
      '          default: 1.5',
      '          minimum: 1',
      '          maximum: 100',
      '          enum: [1.5, 2.5]',
      '',
    ].join('\n')
    const next = patchWorkflowNodeSchemaProperty(
      constrained,
      'generate',
      'page_size',
      { type: 'string' }
    )
    const prop = (
      schemaOf(next).properties as Record<string, Record<string, unknown>>
    ).page_size
    expect(prop).toMatchObject({ type: 'string' })
    expect(prop).not.toHaveProperty('minimum')
    expect(prop).not.toHaveProperty('maximum')
    expect(prop).not.toHaveProperty('enum')
    expect(prop).not.toHaveProperty('default')
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

  it('drops the config value when the new type rejects it (#428 二轮 P2-2)', () => {
    // number→string：config 里既有 42 与 string 类型失配，连带删除
    // ——intake 的类型校验对失配值与孤儿键同样 raise。
    const numericConfig = [
      'key: demo',
      'nodes:',
      '  generate:',
      '    capability: generate_questions',
      '    config_schema:',
      '      type: object',
      '      properties:',
      '        page_size:',
      '          type: number',
      '    config:',
      '      page_size: 42',
      '',
    ].join('\n')
    const next = patchWorkflowNodeSchemaProperty(
      numericConfig,
      'generate',
      'page_size',
      { type: 'string' }
    )
    expect(schemaOf(next)).toMatchObject({
      properties: { page_size: { type: 'string' } },
    })
    expect(configOf(next)).toEqual({})
    expectNoOrphanConfigKeys(next)
  })

  it('keeps the config value when it stays compatible with the new type (#428 二轮 P2-2)', () => {
    // integer→number：42 恰好兼容 number，config 键保留。
    const integerConfig = [
      'key: demo',
      'nodes:',
      '  generate:',
      '    capability: generate_questions',
      '    config_schema:',
      '      type: object',
      '      properties:',
      '        page_size:',
      '          type: integer',
      '    config:',
      '      page_size: 42',
      '',
    ].join('\n')
    const next = patchWorkflowNodeSchemaProperty(
      integerConfig,
      'generate',
      'page_size',
      { type: 'number' }
    )
    expect(configOf(next)).toEqual({ page_size: 42 })
    expectNoOrphanConfigKeys(next)
  })

  it('keeps the config value when the type does not change', () => {
    const next = patchWorkflowNodeSchemaProperty(
      dagYamlWithConfig,
      'generate',
      'bank_version',
      { type: 'string', description: '版本' }
    )
    expect(configOf(next)).toEqual({ bank_version: 'v2', dry_run: true })
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

  it('migrates the node config key along with a rename (#428 P1)', () => {
    const next = renameWorkflowNodeSchemaProperty(
      dagYamlWithConfig,
      'generate',
      'bank_version',
      'bank'
    )
    // config 旧键迁移为新键，值保留；不产生孤儿键。
    expect(configOf(next)).toEqual({ bank: 'v2', dry_run: true })
    expectNoOrphanConfigKeys(next)
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

  it('deletes the node config key along with the property (#428 P1)', () => {
    const next = removeWorkflowNodeSchemaProperty(
      dagYamlWithConfig,
      'generate',
      'dry_run'
    )
    // 被删属性的 config 键一并消失，其余键保留。
    expect(configOf(next)).toEqual({ bank_version: 'v2' })
    expectNoOrphanConfigKeys(next)
  })

  it('drops config entirely when the last property and its value go', () => {
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
      '    config:',
      '      dry_run: true',
      '',
    ].join('\n')
    const next = removeWorkflowNodeSchemaProperty(single, 'generate', 'dry_run')
    const node =
      (yaml.load(next) as { nodes?: Record<string, Record<string, unknown>> })
        .nodes?.generate ?? {}
    // schema 与 config 同步清空：整段 config 不留空壳。
    expect(node).not.toHaveProperty('config_schema')
    expect(node).not.toHaveProperty('config')
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

  it('clears node config when the whole schema block is deleted (#428 P1)', () => {
    const removed = patchWorkflowNodeConfigSchema(
      dagYamlWithConfig,
      'generate',
      undefined
    )
    const node =
      (
        yaml.load(removed) as {
          nodes?: Record<string, Record<string, unknown>>
        }
      ).nodes?.generate ?? {}
    // 整段 schema 删除 = 节点回到无 config 状态，config 全量清空。
    expect(node).not.toHaveProperty('config_schema')
    expect(node).not.toHaveProperty('config')
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
  })

  it('reports unparseable numeric defaults as null instead of silently dropping them (#428 二轮 NIT-2b)', () => {
    // null = 行内报错信号：'abc' 不是任何数字，静默当「删除键」处理
    // 会掩盖用户的输入错误。
    expect(parseSchemaDefaultValue('abc', 'integer')).toBeNull()
    expect(parseSchemaDefaultValue('abc', 'number')).toBeNull()
  })

  it('checks value/type compatibility', () => {
    expect(defaultValueMatchesType('v1', 'string')).toBe(true)
    expect(defaultValueMatchesType('v1', 'integer')).toBe(false)
    expect(defaultValueMatchesType(1.5, 'number')).toBe(true)
    expect(defaultValueMatchesType(1.5, 'integer')).toBe(false)
    expect(defaultValueMatchesType(true, 'boolean')).toBe(true)
    expect(defaultValueMatchesType(1, 'boolean')).toBe(false)
  })

  it('checks stored config values against the new type (#428 二轮 P2-2)', () => {
    // 与后端 config_schema._type_matches 对齐：integer 只收真整数，
    // number 不收 boolean，string 只收 string。
    expect(isConfigValueOfType(42, 'integer')).toBe(true)
    expect(isConfigValueOfType(42, 'number')).toBe(true)
    expect(isConfigValueOfType(1.5, 'integer')).toBe(false)
    expect(isConfigValueOfType(1.5, 'number')).toBe(true)
    expect(isConfigValueOfType('42', 'integer')).toBe(false)
    expect(isConfigValueOfType('v1', 'string')).toBe(true)
    expect(isConfigValueOfType(42, 'string')).toBe(false)
    expect(isConfigValueOfType(true, 'boolean')).toBe(true)
    expect(isConfigValueOfType(true, 'integer')).toBe(false)
    expect(isConfigValueOfType(true, 'number')).toBe(false)
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

describe('config value constraints (#428 codex round 2)', () => {
  it('flags values outside enum / below minimum / above maximum', () => {
    const enumProp = { type: 'string' as const, enum: ['a', 'b'] }
    expect(configValueConstraintError(enumProp, 'a')).toBeNull()
    expect(configValueConstraintError(enumProp, 'c')).toContain('枚举')
    expect(configValueConstraintError(enumProp, undefined)).toBeNull()

    const bounded = { type: 'integer' as const, minimum: 1, maximum: 10 }
    expect(configValueConstraintError(bounded, 5)).toBeNull()
    expect(configValueConstraintError(bounded, 0)).toContain('不得小于 1')
    expect(configValueConstraintError(bounded, 11)).toContain('不得大于 10')
  })

  it('rejects non-integer values for integer properties (#428 codex 二轮 P1)', () => {
    // '1.5' 经 parseConfigValue 的 Number() 得到 1.5：不查整数性会写入
    // 草稿，revision 激活后新 job 在 intake 因后端要求真整数而失败。
    const integerProp = { type: 'integer' as const }
    expect(configValueConstraintError(integerProp, 1)).toBeNull()
    expect(configValueConstraintError(integerProp, 1.5)).toContain('整数')
    expect(configValueConstraintError(integerProp, -0.5)).toContain('整数')
    // number 类型照收小数；字符串/布尔走各自的控件不经此处。
    const numberProp = { type: 'number' as const }
    expect(configValueConstraintError(numberProp, 1.5)).toBeNull()
  })

  it('validates default values against the full constraint set (#428 codex 二轮 P2)', () => {
    // 默认值提交路径复用同一判定：enum 外默认值会让 loader 拒绝整份草稿。
    const enumProp = { type: 'string' as const, enum: ['v1', 'v2'] }
    expect(configValueConstraintError(enumProp, 'v1')).toBeNull()
    expect(configValueConstraintError(enumProp, 'v3')).toContain('枚举')

    const bounded = { type: 'integer' as const, minimum: 1, maximum: 10 }
    expect(configValueConstraintError(bounded, 5)).toBeNull()
    expect(configValueConstraintError(bounded, 0)).toContain('不得小于 1')
    expect(configValueConstraintError(bounded, 11)).toContain('不得大于 10')

    const fractional = { type: 'integer' as const, default: 1 }
    expect(configValueConstraintError(fractional, 1.5)).toContain('整数')
  })

  it('identifies secret properties for form exclusion', () => {
    expect(isSecretConfigProperty({ type: 'string' })).toBe(false)
    expect(isSecretConfigProperty({ type: 'string', secret: true })).toBe(true)
    expect(isSecretConfigProperty({ type: 'string', secret_ref: true })).toBe(
      false
    )
  })
})
