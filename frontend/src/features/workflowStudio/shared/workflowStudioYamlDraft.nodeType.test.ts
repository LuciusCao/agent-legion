import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
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

// 中游审批门：after 里有可执行上游（intake），满足 approval 的入边前提。
const midDagYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    capability: intake',
  '    after: [_start]',
  '  gate:',
  '    type: approval',
  '    config: {rework_target: intake, feedback_artifact: review.json}',
  '    after: [intake]',
  '',
].join('\n')

describe('patchWorkflowNodeType', () => {
  it('switches code→agent without touching other fields', () => {
    const out = patchWorkflowNodeType(baseYaml, 'intake', 'agent')
    const node = parseNodes(out).nodes?.intake
    expect(node?.type).toBe('agent')
    expect(node?.capability).toBe('intake')
    expect(node?.label).toBe('读取知识点')
  })

  it('switches to approval and strips forbidden fields (loader mirror)', () => {
    const target = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake2:',
      '    type: code',
      '    capability: intake2',
      '    after: [_start]',
      '  intake:',
      '    type: agent',
      '    capability: intake',
      '    skill: demo/skill',
      '    execution: {provider: openai, model: gpt-4o}',
      '    config_schema: {foo: {type: string}}',
      '    config: {rework_target: intake2, feedback_artifact: review.json, other: 1}',
      '    after: [intake2]',
      '',
    ].join('\n')
    const out = patchWorkflowNodeType(target, 'intake', 'approval')
    const node = parseNodes(out).nodes?.intake
    expect(node?.type).toBe('approval')
    // _FORBIDDEN_APPROVAL_FIELDS：capability/execution/skill/config_schema。
    expect(node).not.toHaveProperty('capability')
    expect(node).not.toHaveProperty('execution')
    expect(node).not.toHaveProperty('skill')
    expect(node).not.toHaveProperty('config_schema')
    // config 白名单只剩 rework_target / feedback_artifact。
    expect(node?.config).toEqual({
      rework_target: 'intake2',
      feedback_artifact: 'review.json',
    })
  })

  it('keeps executable config keys when switching code↔agent (P2: keys are not reserved)', () => {
    // 可执行节点的 config 键由其 config_schema 决定；rework_target 等并非
    // 全局保留字，code↔agent 互切不得动它们。
    const codeWithConfig = baseYaml.replace(
      '    after: [_start]',
      '    config: {rework_target: intake, feedback_artifact: review.json}\n    after: [_start]'
    )
    const toAgent = patchWorkflowNodeType(codeWithConfig, 'intake', 'agent')
    expect(parseNodes(toAgent).nodes?.intake?.config).toEqual({
      rework_target: 'intake',
      feedback_artifact: 'review.json',
    })
    const backToCode = patchWorkflowNodeType(toAgent, 'intake', 'code')
    expect(parseNodes(backToCode).nodes?.intake?.config).toEqual({
      rework_target: 'intake',
      feedback_artifact: 'review.json',
    })
  })

  it('refuses approval→code/agent without a capability (no half-applied state)', () => {
    // approval 节点按契约无 capability；loader 对 code/agent 要求非空。
    // 前置校验必须拦下，否则草稿落入「type 已改、capability 缺失」的
    // 不可发布半应用态（AGENTS.md L88）。被拦截时草稿原样保留——审批
    // config 键的剥除因此只发生在「先补 capability 再切」的路径上。
    expect(() => patchWorkflowNodeType(midDagYaml, 'gate', 'code')).toThrow(
      WorkflowNodeTypeSwitchError
    )
    expect(() => patchWorkflowNodeType(midDagYaml, 'gate', 'agent')).toThrow(
      WorkflowNodeTypeSwitchError
    )
    // 用户在「基本设置」补齐 capability 后切换成功，审批专属 config 键
    // 随切换剥除（空 config 整体删除）。
    const withCapability = midDagYaml.replace(
      '    type: approval',
      '    type: approval\n    capability: gate_cap'
    )
    const out = patchWorkflowNodeType(withCapability, 'gate', 'code')
    const node = parseNodes(out).nodes?.gate
    expect(node?.type).toBe('code')
    expect(node?.capability).toBe('gate_cap')
    expect(node).not.toHaveProperty('config')
  })

  it('refuses →approval without an executable upstream (validate_approval_edges mirror)', () => {
    // 仅 start 驱动的根节点切 approval：start 的合成边不算可执行上游。
    const rootCodeYaml = baseYaml
    expect(() =>
      patchWorkflowNodeType(rootCodeYaml, 'intake', 'approval')
    ).toThrow(WorkflowNodeTypeSwitchError)
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
