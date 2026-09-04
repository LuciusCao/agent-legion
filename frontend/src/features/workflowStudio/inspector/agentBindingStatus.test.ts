import { describe, expect, it } from 'vitest'
import { bindingStatus } from './agentBindingStatus'

// #426 codex 终轮 P2：bindingStatus 门控的纯函数单测——门控语义的权威
// 表（catalog 命中 published 不等 definitions；未命中须两份查询 settle）。
// settle 的来源信号（useAgentCatalog 两份查询）由 useAgentCatalog.test.tsx
// 覆盖，本文件只测组合逻辑。

/** catalog/definitions 双双 settle 的基线信号。 */
const settled = {
  catalogSettled: true,
  catalogFailed: false,
  definitionsSettled: true,
  definitionsFailed: false,
}

const catalogPending = { ...settled, catalogSettled: false }
const definitionsPending = { ...settled, definitionsSettled: false }
const catalogFailed = { ...settled, catalogFailed: true }
const definitionsFailed = { ...settled, definitionsFailed: true }

// useCapabilityAgent 的解析结果：published 命中（agent 非空且非 draft 回落）
// vs 未命中（agent 缺省或 draft 回落）。
const publishedHit = { agent: { id: 'agent-v1' }, isDraft: false }
const draftFallback = { agent: { id: 'draft-a' }, isDraft: true }
const unbound = { agent: undefined, isDraft: false }

describe('bindingStatus', () => {
  // 场景 1：catalog 空/未命中 + definitions pending → 占位（codex 终轮 P2
  // 本体——空 catalog 先回时 agentId=null 只是「未知」，不能放出新建表单）。
  it('reports pending when the catalog settles empty while definitions are still loading', () => {
    expect(bindingStatus(unbound, definitionsPending)).toBe('pending')
    expect(bindingStatus(draftFallback, definitionsPending)).toBe('pending')
  })

  // 场景 2：catalog 空/未命中 + definitions settle（无 draft）→ ready，新建
  // 表单（未绑定已是终态）。
  it('reports ready for an unbound capability once both queries settle', () => {
    expect(bindingStatus(unbound, settled)).toBe('ready')
  })

  // 场景 3：catalog 空/未命中 + definitions settle（有 draft）→ ready，编辑
  // 该 draft（回落终态）。
  it('reports ready for a draft fallback once both queries settle', () => {
    expect(bindingStatus(draftFallback, settled)).toBe('ready')
  })

  // 场景 4：catalog 命中 published → 不等 definitions 直接 ready（编辑器按
  // ID 加载详情不依赖列表；definitions 在途/失败由 loadError 横幅兜底）。
  it('reports ready on a published hit without waiting for definitions', () => {
    expect(bindingStatus(publishedHit, definitionsPending)).toBe('ready')
    expect(bindingStatus(publishedHit, definitionsFailed)).toBe('ready')
  })

  // catalog 未 settle → 一律 pending（draft 回落先行场景：settle 后可能被
  // 同 capability 的 published Agent 替换，先放行会丢输入/撞发布冲突）。
  it('reports pending while the catalog itself is loading', () => {
    expect(bindingStatus(publishedHit, catalogPending)).toBe('pending')
    expect(bindingStatus(unbound, catalogPending)).toBe('pending')
  })

  // catalog 失败且无数据 → error（不落回可操作表单）；definitions 失败仅在
  // 未命中 published 时阻断门控（命中侧已由上一用例覆盖走 ready）。
  it('reports error when a required query failed without data', () => {
    expect(bindingStatus(publishedHit, catalogFailed)).toBe('error')
    expect(bindingStatus(unbound, catalogFailed)).toBe('error')
    expect(bindingStatus(unbound, definitionsFailed)).toBe('error')
  })
})
