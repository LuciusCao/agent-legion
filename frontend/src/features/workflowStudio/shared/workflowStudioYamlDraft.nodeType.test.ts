import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  patchWorkflowNodeType,
  workflowNodeKindBadge,
} from './workflowStudioYamlDraft.nodeType'

// 字段清洗规则的蓝本是后端 loader 的类型禁令：approval 对拍
// tests/workflows/test_approval_node_definition.py（_FORBIDDEN_APPROVAL_FIELDS
// / _ALLOWED_CONFIG_KEYS），code 禁 skill 对拍 EXEC-SKILL-NODE-001。
const baseYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    label: 读取知识点',
  '    capability: intake',
  '    after: [_start]',
  '',
].join('\n')

type RawYaml = { nodes?: Record<string, Record<string, unknown>> }

// 直接用 js-yaml 解析改写结果，避免依赖 parse 层自身的归一化掩盖问题。
function parseNodes(raw: string): RawYaml {
  return yaml.load(raw) as RawYaml
}

describe('patchWorkflowNodeType', () => {
  it('switches code→agent without touching other fields', () => {
    const out = patchWorkflowNodeType(baseYaml, 'intake', 'agent')
    const node = parseNodes(out).nodes?.intake
    expect(node?.type).toBe('agent')
    expect(node?.capability).toBe('intake')
    expect(node?.label).toBe('读取知识点')
  })

  it('switches to approval and strips forbidden fields (loader mirror)', () => {
    const yaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake:',
      '    type: agent',
      '    capability: intake',
      '    skill: demo/skill',
      '    execution: {provider: openai, model: gpt-4o}',
      '    config_schema: {foo: {type: string}}',
      '    config: {rework_target: _start, feedback_artifact: review.json, other: 1}',
      '    after: [_start]',
      '',
    ].join('\n')
    const out = patchWorkflowNodeType(yaml, 'intake', 'approval')
    const node = parseNodes(out).nodes?.intake
    expect(node?.type).toBe('approval')
    // _FORBIDDEN_APPROVAL_FIELDS：capability/execution/skill/config_schema。
    expect(node).not.toHaveProperty('capability')
    expect(node).not.toHaveProperty('execution')
    expect(node).not.toHaveProperty('skill')
    expect(node).not.toHaveProperty('config_schema')
    // config 白名单只剩 rework_target / feedback_artifact。
    expect(node?.config).toEqual({
      rework_target: '_start',
      feedback_artifact: 'review.json',
    })
  })

  it('switches approval→code keeping only non-approval config keys and dropping skill', () => {
    const yaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  gate:',
      '    type: approval',
      '    config: {rework_target: _start, feedback_artifact: review.json}',
      '    after: [_start]',
      '',
    ].join('\n')
    const out = patchWorkflowNodeType(yaml, 'gate', 'code')
    const node = parseNodes(out).nodes?.gate
    expect(node?.type).toBe('code')
    // code 节点需要 capability（loader 要求非空）；由用户在编辑器里补。
    // approval 专属 config 键剥除（空 config 整体删除）。
    expect(node).not.toHaveProperty('config')
  })

  it('drops skill when switching agent→code (EXEC-SKILL-NODE-001)', () => {
    const yaml = baseYaml.replace(
      '    capability: intake',
      '    capability: intake\n    skill: demo/skill'
    )
    const out = patchWorkflowNodeType(yaml, 'intake', 'code')
    expect(parseNodes(out).nodes?.intake).not.toHaveProperty('skill')
  })

  it('refuses to patch a start node (fail-closed)', () => {
    expect(() => patchWorkflowNodeType(baseYaml, '_start', 'code')).toThrow(
      'start node'
    )
  })

  it('throws for unknown nodes', () => {
    expect(() => patchWorkflowNodeType(baseYaml, 'nope', 'agent')).toThrow(
      'not found'
    )
  })
})

describe('workflowNodeKindBadge', () => {
  it('maps types to badges (agent renders none)', () => {
    expect(workflowNodeKindBadge('agent')).toBe('')
    expect(workflowNodeKindBadge('approval')).toBe('approval')
    expect(workflowNodeKindBadge('code')).toBe('code')
    expect(workflowNodeKindBadge(undefined)).toBe('code')
  })
})
