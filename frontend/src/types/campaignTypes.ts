import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

/**
 * 批量任务（campaign，#532 / #505 PR-D）的 transport 类型，全部从生成的
 * API 契约派生（AGENTS.md：禁止手写 transport types）。对用户的产品名是
 * 「批量任务」；campaign 只在代码/接口层存在，不进 UI 文案。
 */
export type CampaignRecord = ApiSchemas['CampaignRecord']
export type CampaignCreateRequest = ApiSchemas['CampaignCreateRequest']
export type CampaignCreateResponse = ApiSchemas['CampaignCreateResponse']
export type CampaignDetailResponse = ApiSchemas['CampaignDetailResponse']
export type CampaignListResponse = ApiSchemas['CampaignListResponse']
export type CampaignPreviewRequest = ApiSchemas['CampaignPreviewRequest']
export type CampaignPreviewResponse = ApiSchemas['CampaignPreviewResponse']
export type CampaignRerunTarget = ApiSchemas['CampaignRerunTarget']
export type CampaignSubmitInlineTarget =
  ApiSchemas['CampaignSubmitInlineTarget']
export type CampaignRerunPreviewResult =
  ApiSchemas['CampaignRerunPreviewResult']
export type CampaignSubmitPreviewResult =
  ApiSchemas['CampaignSubmitPreviewResult']
export type CampaignStatusChangeResponse =
  ApiSchemas['CampaignStatusChangeResponse']
export type CampaignStatus = CampaignRecord['status']
export type CampaignMode = CampaignRecord['mode']

/** progress_json 的水位采样（feeder 每 CAS 推进追加的 {level, ts}）。 */
export type CampaignWatermarkSample = { level: number; ts: number }

/**
 * submit 模式详情聚合里的关联 run 概览。PR-A 契约还没有这个 schema
 * （PR-C 落地 detail 聚合时生成）：这里从响应的 runs 形状派生为可选字段，
 * 避免在 PR-C 合入前后手写漂移——列表/详情组件统一按「可能缺席」读取。
 */
export type CampaignRunOverview =
  NonNullable<CampaignDetailResponse['campaign']> extends { runs?: infer R }
    ? R extends readonly (infer Item)[]
      ? Item
      : never
    : never
