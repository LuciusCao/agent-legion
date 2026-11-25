import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import { isSecretRefConfigValue } from './workflowStudioYamlDraft.configSchema.configLink'
import { patchWorkflowNodeSchemaProperty } from './workflowStudioYamlDraft.configSchema.properties'

function configOf(raw: string): Record<string, unknown> {
  const node = ((
    yaml.load(raw) as { nodes?: Record<string, Record<string, unknown>> }
  ).nodes?.generate ?? {}) as { config?: Record<string, unknown> }
  return node.config ?? {}
}

describe('secret and scalar type transitions (#1135 r1)', () => {
  it.each([
    ['string', 'plain text', 'plain text'],
    ['integer', 42, '42'],
    ['number', 1.5, '1.5'],
    ['boolean', true, 'true'],
  ])(
    'secret true→false drops Vault dicts and preserves valid %s scalars',
    (type, scalar, scalarYaml) => {
      const secretYaml = [
        'key: demo',
        'nodes:',
        '  generate:',
        '    config_schema:',
        '      properties:',
        '        token:',
        `          type: ${type}`,
        '          secret: true',
        '    config:',
        '      token: {secret_ref: node:demo:generate:token}',
        '',
      ].join('\n')
      const unsecret = patchWorkflowNodeSchemaProperty(
        secretYaml,
        'generate',
        'token',
        { secret: false }
      )
      expect(configOf(unsecret)).toEqual({})

      const legacyScalar = secretYaml.replace(
        '{secret_ref: node:demo:generate:token}',
        scalarYaml
      )
      const preserved = patchWorkflowNodeSchemaProperty(
        legacyScalar,
        'generate',
        'token',
        { secret: false }
      )
      expect(configOf(preserved).token).toEqual(scalar)
    }
  )

  it('accepts only single-key, non-empty node secret_ref markers', () => {
    expect(
      isSecretRefConfigValue({ secret_ref: 'node:demo:generate:token' })
    ).toBe(true)
    for (const malformed of [
      { secret_ref: 'node:demo:generate:token', extra: true },
      { secret_ref: '' },
      { secret_ref: 'node:' },
      { secret_ref: 'conn:cms:token' },
      { secret_ref: 42 },
      null,
      [],
    ]) {
      expect(isSecretRefConfigValue(malformed)).toBe(false)
    }
  })

  it('keeps repeated secret and type transitions scalar-safe with YAML roundtrips', () => {
    const secretYaml = `key: demo
nodes:
  generate:
    execution: {runtime: pi, model: your-model-b}
    skill: {key: education/generate, ref: latest}
    config_schema:
      properties:
        token: {type: string, secret: true}
    config:
      token: {secret_ref: node:demo:generate:token}
`
    let next = patchWorkflowNodeSchemaProperty(
      secretYaml,
      'generate',
      'token',
      { type: 'integer' }
    )
    expect(configOf(next).token).toEqual({
      secret_ref: 'node:demo:generate:token',
    })
    next = patchWorkflowNodeSchemaProperty(next, 'generate', 'token', {
      secret: false,
    })
    expect(configOf(next)).toEqual({})
    next = patchWorkflowNodeSchemaProperty(next, 'generate', 'token', {
      secret: true,
      type: 'string',
    })
    next = patchWorkflowNodeSchemaProperty(next, 'generate', 'token', {
      secret: false,
    })
    const roundtrip = yaml.load(next) as {
      nodes?: Record<string, Record<string, unknown>>
    }
    expect(roundtrip.nodes?.generate?.execution).toEqual({
      runtime: 'pi',
      model: 'your-model-b',
    })
    expect(roundtrip.nodes?.generate?.skill).toEqual({
      key: 'education/generate',
      ref: 'latest',
    })
    expect(roundtrip.nodes?.generate?.config).toBeUndefined()
    expect(next).not.toContain('••••')
    expect(next).not.toContain('plaintext-token')
  })
})
