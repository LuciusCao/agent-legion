import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
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
    // config 键的剥除因此只发生在「补 capability 再切」的路径上。
    expect(() => patchWorkflowNodeType(midDagYaml, 'gate', 'code')).toThrow(
      WorkflowNodeTypeSwitchError
    )
    expect(() => patchWorkflowNodeType(midDagYaml, 'gate', 'agent')).toThrow(
      WorkflowNodeTypeSwitchError
    )
    // 手写「approval + capability」的中间态 YAML 仍是合法直通路径
    // （已有 capability 优先于补能力通道）。
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

  it('switches approval→code atomically via the capability channel (#405)', () => {
    // 结构化 UI 对 approval 隐藏能力 Key 输入，「先在基本设置补再切」
    // 不可达；切换弹窗收集的 capability 随本次 patch 原子写入——type
    // 与 capability 一次提交，无中间非法态。
    const out = patchWorkflowNodeType(midDagYaml, 'gate', 'code', 'gate_cap')
    const node = parseNodes(out).nodes?.gate
    expect(node?.type).toBe('code')
    expect(node?.capability).toBe('gate_cap')
    // 审批专属 config 键仍随切换剥除（空 config 整体删除）。
    expect(node).not.toHaveProperty('config')
    const toAgent = patchWorkflowNodeType(
      midDagYaml,
      'gate',
      'agent',
      'gate_cap'
    )
    expect(parseNodes(toAgent).nodes?.gate?.type).toBe('agent')
    expect(parseNodes(toAgent).nodes?.gate?.capability).toBe('gate_cap')
    // 空白通道等同未提供（弹窗留空不应绕过前置校验）。
    expect(() =>
      patchWorkflowNodeType(midDagYaml, 'gate', 'code', '   ')
    ).toThrow(WorkflowNodeTypeSwitchError)
  })

  it('refuses →approval without an executable upstream (validate_approval_edges mirror)', () => {
    // 仅 start 驱动的根节点切 approval：start 的合成边不算可执行上游。
    expect(() => patchWorkflowNodeType(baseYaml, 'intake', 'approval')).toThrow(
      WorkflowNodeTypeSwitchError
    )
  })

  it('accepts →approval when the upstream is declared via edges only (v2 yaml)', () => {
    // 手写 v2 YAML 用 edges 声明依赖、after 只是 echo（甚至缺省）——判定源
    // 必须是 after ∪ 进入本节点的 edges（取 from 侧），与
    // validate_approval_edges 的物化 edges 同构。
    const edgesOnlyYaml = [
      'key: demo',
      'schema_version: 2',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake2:',
      '    type: code',
      '    capability: intake2',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      'edges:',
      '  - {from: _start, to: intake2}',
      '  - {from: intake2, to: gate}',
      '',
    ].join('\n')
    const out = patchWorkflowNodeType(edgesOnlyYaml, 'gate', 'approval')
    expect(parseNodes(out).nodes?.gate?.type).toBe('approval')
    // 反向：edges 里只有 start 驱动的边时仍拦截。
    const startOnlyEdgesYaml = [
      'key: demo',
      'schema_version: 2',
      'nodes:',
      '  _start:',
      '    type: start',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      'edges:',
      '  - {from: _start, to: gate}',
      '',
    ].join('\n')
    expect(() =>
      patchWorkflowNodeType(startOnlyEdgesYaml, 'gate', 'approval')
    ).toThrow(WorkflowNodeTypeSwitchError)
  })

  it('rejects →approval when the draft edge does not enter this node (#405)', () => {
    // #405：收集全图 edge.to 会把「别处存在一条非 start 边」误判为入边
    // （判定退化为任意非 start 边即可通过）。入边判定必须约束
    // edge.to === 当前节点、收集 edge.from；本用例里唯一的非 start 边
    // （draft_gen → intake）不进入 gate，gate 没有可执行入边仍拦截。
    const unrelatedEdgeYaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  draft_gen:',
      '    type: code',
      '    capability: draft_gen',
      '    after: [_start]',
      '  intake:',
      '    type: code',
      '    capability: intake',
      '    after: [draft_gen]',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      '    after: [_start]',
      'edges:',
      '  - {from: _start, to: draft_gen}',
      '  - {from: draft_gen, to: intake}',
      '',
    ].join('\n')
    expect(() =>
      patchWorkflowNodeType(unrelatedEdgeYaml, 'gate', 'approval')
    ).toThrow(WorkflowNodeTypeSwitchError)
    // 对照：进入本节点的非 start 边才放行。
    const incomingEdgeYaml = unrelatedEdgeYaml.replace(
      '  - {from: draft_gen, to: intake}',
      '  - {from: draft_gen, to: intake}\n  - {from: intake, to: gate}'
    )
    const out = patchWorkflowNodeType(incomingEdgeYaml, 'gate', 'approval')
    expect(parseNodes(out).nodes?.gate?.type).toBe('approval')
  })

  it('v3+ counts only entering edges like v2 (#606 codex P2)', () => {
    // loader 对 v2+ 一律 edges-only（物化只在 v1）；仓库 compare 测试
    // 明确接受版本 3——判定不得把 v3 的 after 当入边。
    const v3Yaml = [
      'key: demo',
      'schema_version: 3',
      'nodes:',
      '  _start:',
      '    type: start',
      '  draft_gen:',
      '    type: code',
      '    capability: draft_gen',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      '    after: [draft_gen]',
      'edges:',
      '  - {from: _start, to: draft_gen}',
      '',
    ].join('\n')
    expect(() => patchWorkflowNodeType(v3Yaml, 'gate', 'approval')).toThrow(
      WorkflowNodeTypeSwitchError
    )
  })

  it('v2 counts only entering edges — after is an echo field (#405 审核 P2)', () => {
    // v2 草稿 loader 只以 edges 列表为准（_load_edges 不物化 after）；
    // gate 有 after: [draft_gen] 但无入边——无条件计入 after 会让前端
    // 放行、发布才被 validate_approval_edges 拒（正是要消除的漂移）。
    const v2AfterNoEdgeYaml = [
      'key: demo',
      'schema_version: 2',
      'nodes:',
      '  _start:',
      '    type: start',
      '  draft_gen:',
      '    type: code',
      '    capability: draft_gen',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      '    after: [draft_gen]',
      'edges:',
      '  - {from: _start, to: draft_gen}',
      '',
    ].join('\n')
    expect(() =>
      patchWorkflowNodeType(v2AfterNoEdgeYaml, 'gate', 'approval')
    ).toThrow(WorkflowNodeTypeSwitchError)
    // 对照：v1（无 schema_version 声明）after 物化为边，同形状放行。
    const v1SameShape = v2AfterNoEdgeYaml
      .replace('schema_version: 2\n', '')
      .replace('  - {from: _start, to: draft_gen}\n', '')
    const outV1 = patchWorkflowNodeType(v1SameShape, 'gate', 'approval')
    expect(parseNodes(outV1).nodes?.gate?.type).toBe('approval')
  })

  it('upstream keys must exist in nodes — unknown sources stay refused (#405 审核 P2)', () => {
    // loader 对未知边来源/依赖单独拒绝；ghost 上游不得作为入边放行。
    const ghostUpstreamYaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  gate:',
      '    type: code',
      '    capability: gate_cap',
      '    after: [ghost_node]',
      '',
    ].join('\n')
    expect(() =>
      patchWorkflowNodeType(ghostUpstreamYaml, 'gate', 'approval')
    ).toThrow(WorkflowNodeTypeSwitchError)
  })

  it('drops skill when switching agent→code (EXEC-SKILL-NODE-001)', () => {
    // 源节点必须是 type: agent（同类型 code→code 不经选择器发生）。
    const agentYaml = baseYaml.replace('    type: code', '    type: agent')
    const out = patchWorkflowNodeType(agentYaml, 'intake', 'code')
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
