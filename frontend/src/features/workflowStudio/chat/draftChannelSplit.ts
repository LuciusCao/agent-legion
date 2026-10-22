import {
  diffRuntimeSliceKeys,
  diffStructuralSummary,
  extractRuntimeSlice,
  type RuntimeSlice,
  type StructuralDiffSummary,
} from '../shared/workflowRuntimeSlice'

/** M65 拆卡：agent 的 workflow 建议按通道拆成两张独立卡片共用的通道计算
 * （口径与 M48 卡内双分组一致）——runtime diff 相对 active 基线
 * （originalYaml，最新生效配置），结构 diff 相对当前草稿（currentDraftYaml，
 * 回落 originalYaml）。无 originalYaml（无 studio 上下文 / 未发布过）或建议
 * YAML 解析失败时返回 null，调用方回落经典单卡形态。 */
export type DraftChannels = {
  proposal: RuntimeSlice
  runtimeKeys: string[]
  structural: StructuralDiffSummary
  structuralCount: number
}

export function computeDraftChannels(args: {
  proposalYaml: string
  originalYaml: string | undefined
  currentDraftYaml: string
}): DraftChannels | null {
  if (!args.originalYaml) return null
  try {
    const proposal = extractRuntimeSlice(args.proposalYaml)
    const structural = diffStructuralSummary(
      args.currentDraftYaml || args.originalYaml,
      args.proposalYaml
    )
    return {
      proposal,
      runtimeKeys: diffRuntimeSliceKeys(
        extractRuntimeSlice(args.originalYaml),
        proposal
      ),
      structural,
      structuralCount:
        structural.nodeKeys.length +
        (structural.edgesChanged ? 1 : 0) +
        (structural.otherChanged ? 1 : 0),
    }
  } catch {
    return null
  }
}

/** runtime diff 键在建议切片上的新值（diff 行「field → value」展示用）。 */
export function runtimeValueAt(slice: RuntimeSlice, key: string): string {
  const parts = key.split('.')
  if (parts[0] === 'execution') {
    const field = parts[1] as keyof RuntimeSlice['execution']
    return slice.execution[field] || '（清除）'
  }
  const node = slice.nodes[parts[1]]
  if (!node) return ''
  const field = parts[2]
  if (field === 'tools') return node.tools.join(', ') || '（清除）'
  if (field === 'requires_labels') {
    if (parts.length > 3) {
      return node.requires_labels[parts.slice(3).join('.')] || '（清除）'
    }
    return (
      Object.entries(node.requires_labels)
        .map(([k, v]) => (v ? `${k}=${v}` : k))
        .join(', ') || '（清除）'
    )
  }
  const value =
    node[
      field as keyof Omit<
        RuntimeSlice['nodes'][string],
        'tools' | 'requires_labels'
      >
    ]
  return typeof value === 'string' && value ? value : '（清除）'
}

export function formatRuntimeKey(key: string): string {
  return key.startsWith('nodes.')
    ? key.slice('nodes.'.length)
    : key.replace(/^execution\./, '顶层 ')
}

/** 运行配置卡的落定记录键：优先草稿语义身份（definition_hash，#1143），
 * 缺失（旧转录）时回落 yaml 内容哈希——同一张卡的 key 在会话内稳定即可。 */
export function runtimeCardKey(draft: {
  yaml: string
  draftHash: string | null
}): string {
  return draft.draftHash ?? `yaml-${hashString(draft.yaml)}`
}

/** 落定记录的 store 键（评审 P2-2）：内容派生键加 workspaceId 前缀——
 * 同一 tab 内两个 workspace 产出字节级相同建议时，落定记录不跨 workspace
 * 串扰（版本号分属各 workspace 的配置版本序列）。 */
export function runtimeCardSettlementKey(
  workspaceId: string,
  draft: { yaml: string; draftHash: string | null }
): string {
  return `${workspaceId}:${runtimeCardKey(draft)}`
}

function hashString(text: string): string {
  let hash = 5381
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0
  }
  return (hash >>> 0).toString(36)
}
