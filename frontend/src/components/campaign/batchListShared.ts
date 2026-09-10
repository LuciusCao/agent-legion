import type { CampaignRecord } from '../../types/campaignTypes'
import { batchModeLabel } from './BatchStatusBadge'

/**
 * 列表/详情共用的小件：批量任务的显示名与副行说明（拆出来避免
 * List ↔ Detail 循环 import——Detail 在 List 的展开行里渲染）。
 */

/**
 * 批量任务的显示名。定稿：列表只显示人话名，ID 进详情；name 为空（旧
 * 数据或缺省创建）时由类型 + 创建时间派生（「重跑 · 09-09 14:02」样式，
 * 与创建入口的默认命名同口径）。
 */
export function batchDisplayName(campaign: CampaignRecord): string {
  if (campaign.name) return campaign.name
  const date = campaign.created_at ? new Date(campaign.created_at) : new Date()
  const stamp = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate()
  ).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes()
  ).padStart(2, '0')}`
  return `${batchModeLabel(campaign.mode)} · ${stamp}`
}

/** 副行说明：类型 + 目标摘要（清单条数 / 筛选范围）。 */
export function batchSubLabel(campaign: CampaignRecord): string {
  const spec = campaign.target_spec ?? {}
  if (campaign.mode === 'submit') {
    if (Array.isArray(spec.items)) {
      return `清单 ${spec.items.length.toLocaleString()} 条 · 粘贴`
    }
    if (typeof spec.manifest_item_count === 'number') {
      return `清单 ${spec.manifest_item_count.toLocaleString()} 条 · 文件上传`
    }
    return '新建任务'
  }
  if (Array.isArray(spec.job_ids)) {
    return `指定 ${spec.job_ids.length.toLocaleString()} 个任务`
  }
  return '按当前筛选条件全量执行'
}

/** 「N 成功 / N 跳过 / N 失败」计数摘要（列表行与详情共用口径）。 */
export function batchCountsLabel(campaign: CampaignRecord): string {
  return `成功 ${campaign.jobs_succeeded.toLocaleString()} · 跳过 ${campaign.jobs_skipped.toLocaleString()} · 失败 ${campaign.jobs_failed.toLocaleString()}`
}
