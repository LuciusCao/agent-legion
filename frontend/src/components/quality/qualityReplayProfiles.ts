import type {
  QualityReplay,
  QualityReplayCreateRequest,
  QualityReplayProfileOption,
} from '../../api/qualityApi'

// #1079（#440 D6）：回放执行档案的选择与标签——质量回放按 workflow
// revision（或当前草稿）选执行档案，取代旧的「选 Agent 版本」。

/** 原运行的执行档案标签：legacy 节点显示其 Agent 版本，否则即原执行档案。 */
export function originalLabel(agentVersion: number | null | undefined): string {
  return agentVersion != null ? `Agent v${agentVersion}` : '原执行档案'
}

/** replay 跑的执行档案（#1079 / #440 D6）：revision / 草稿 / legacy Agent 版本。 */
export function replayLabel(replay: QualityReplay): string {
  if (replay.revision_version != null)
    return `revision v${replay.revision_version}`
  if (replay.profile_hash) return replay.revision_id ? 'revision' : '草稿'
  if (replay.agent_version != null) return `Agent v${replay.agent_version}`
  return '原执行档案'
}

/** 默认选项文案（#1079 review）：legacy 样本回放原运行实际跑的 Agent 版本
 * （后端按样本记录的版本 pin），文案标明版本，不再隐式跑当前 published。 */
export function originalChoiceLabel(agentVersion: number | null | undefined) {
  return agentVersion != null
    ? `原运行的执行档案（Agent v${agentVersion}）`
    : '原运行的执行档案'
}

export const ORIGINAL_CHOICE = ''
export const DRAFT_CHOICE = 'draft'

export function optionValue(option: QualityReplayProfileOption): string {
  return option.source === 'draft'
    ? DRAFT_CHOICE
    : `revision:${option.revision_id}`
}

export function optionLabel(option: QualityReplayProfileOption): string {
  const head =
    option.source === 'draft'
      ? '当前草稿'
      : `v${option.revision_version}${option.revision_status === 'active' ? '（当前生效）' : ''}`
  const profile = [option.runtime, option.model].filter(Boolean).join(' / ')
  const original = option.is_original ? ' · 与原运行一致' : ''
  return `${head} · ${profile || '（未声明模型）'}${original}`
}

export function createBody(choice: string): QualityReplayCreateRequest {
  if (choice === DRAFT_CHOICE) return { use_draft: true }
  if (choice.startsWith('revision:'))
    return { revision_id: choice.slice('revision:'.length), use_draft: false }
  return { use_draft: false }
}
