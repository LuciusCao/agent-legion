import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

// materials-and-runs 设计的材料与运行 transport 类型，全部从生成的
// API 契约派生（AGENTS.md：禁止手写 transport types）。
export type MaterialRecord = ApiSchemas['MaterialRecord']
export type MaterialPresignRequest = ApiSchemas['MaterialPresignRequest']
export type MaterialPresignResponse = ApiSchemas['MaterialPresignResponse']
export type MaterialResponse = ApiSchemas['MaterialResponse']
export type RunItemMaterial = ApiSchemas['RunItemMaterial']
export type RunItemRef = ApiSchemas['RunItemRef']
export type RunItem = RunItemMaterial | RunItemRef
export type RunCreateRequest = ApiSchemas['RunCreateRequest']
export type RunCreateResponse = ApiSchemas['RunCreateResponse']
export type RunRecord = ApiSchemas['RunRecord']
