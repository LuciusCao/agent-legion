import {
  dumpWorkflowYaml,
  normalizeStringList,
  parseWorkflowYaml,
  type WorkflowYamlNode,
  type WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'
import { asConfigValue } from './workflowStudioYamlDraft.execution'

/**
 * 运行通道 runtime 切片工具（M48）——前端镜像后端
 * server/app/services/runtime_config.py 的语义：
 * - 节点级 runtime 键：execution.{provider,model,thinking,prompt,prompt_mode}
 *   + tools + requires_labels（execution.runtime 是结构键，永远不动）；
 * - 顶层 execution 已退役，不读取、不合并，也不随运行通道保存。
 * 用途：①运行配置保存请求体 = 基线结构 + 草稿 runtime 切片；
 * ②运行配置 diff（保存按钮启停、「已修改」标记、发布复核框信息条）；
 * ③结构改动判定（草稿是否只剩 runtime 差异）。
 */

export type RuntimeSliceNode = {
  provider: string
  model: string
  thinking: string
  prompt: string
  prompt_mode: string
  tools: string[]
  requires_labels: Record<string, string>
}

export type RuntimeSlice = {
  nodes: Record<string, RuntimeSliceNode>
}

const NODE_EXECUTION_KEYS = [
  'provider',
  'model',
  'thinking',
  'prompt',
  'prompt_mode',
] as const
type YamlWithTopExecution = WorkflowYamlObject & {
  execution?: Record<string, unknown>
}

/** 顶层 execution 只用于拒绝 authoring 写入，绝不作为运行时输入。 */
export function hasTopLevelExecution(rawYaml: string): boolean {
  const doc = parseWorkflowYaml(rawYaml) as YamlWithTopExecution
  return doc.execution !== undefined
}

function normalizeLabels(value: unknown): Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return {}
  }
  const labels: Record<string, string> = {}
  for (const [key, raw] of Object.entries(value)) {
    labels[key] = typeof raw === 'string' ? raw : ''
  }
  return labels
}

function normalizeNode(node: WorkflowYamlNode | undefined): RuntimeSliceNode {
  const execution = node?.execution ?? {}
  return {
    provider: asConfigValue(execution.provider),
    model: asConfigValue(execution.model),
    thinking: asConfigValue(execution.thinking),
    prompt: asConfigValue(execution.prompt),
    prompt_mode: asConfigValue(execution.prompt_mode),
    tools: normalizeStringList(node?.tools),
    requires_labels: normalizeLabels(node?.requires_labels),
  }
}

/** 完整归一化的 runtime 切片（含空值）——与后端 extract_runtime_config 同形。 */
export function extractRuntimeSlice(rawYaml: string): RuntimeSlice {
  const doc = parseWorkflowYaml(rawYaml)
  const nodes: Record<string, RuntimeSliceNode> = {}
  for (const [key, node] of Object.entries(doc.nodes ?? {})) {
    nodes[key] = normalizeNode(node)
  }
  return { nodes }
}

function flattenSlice(
  value: Record<string, unknown>,
  prefix: string,
  out: Map<string, string>
) {
  for (const [key, item] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (Array.isArray(item)) {
      out.set(path, JSON.stringify(item))
    } else if (typeof item === 'object' && item !== null) {
      flattenSlice(item as Record<string, unknown>, path, out)
    } else {
      out.set(path, String(item))
    }
  }
}

/**
 * 切片 diff：返回排序后的 nodes.<key>.<field> 点路径，列表/mapping
 * 整体比较——与后端 diff_runtime_config_keys 同口径。
 * 只覆盖两侧都存在的节点：草稿里新增/删除的节点属结构改动，其 runtime
 * 键不参与运行配置保存的启停判定（基线不认识的节点保存时本就被忽略）。
 */
export function diffRuntimeSliceKeys(
  a: RuntimeSlice,
  b: RuntimeSlice
): string[] {
  const sharedNodes: RuntimeSlice = {
    nodes: Object.fromEntries(
      Object.keys(a.nodes)
        .filter((key) => key in b.nodes)
        .map((key) => [key, a.nodes[key]])
    ),
  }
  const sharedNodesB: RuntimeSlice = {
    nodes: Object.fromEntries(
      Object.keys(b.nodes)
        .filter((key) => key in sharedNodes.nodes)
        .map((key) => [key, b.nodes[key]])
    ),
  }
  const flatA = new Map<string, string>()
  const flatB = new Map<string, string>()
  flattenSlice(sharedNodes as unknown as Record<string, unknown>, '', flatA)
  flattenSlice(sharedNodesB as unknown as Record<string, unknown>, '', flatB)
  const keys = new Set([...flatA.keys(), ...flatB.keys()])
  return [...keys].filter((key) => flatA.get(key) !== flatB.get(key)).sort()
}

function applyRuntimeToNode(node: WorkflowYamlNode, source: RuntimeSliceNode) {
  const execution = { ...(node.execution ?? {}) }
  for (const key of NODE_EXECUTION_KEYS) {
    // 与后端 apply_runtime_config 对齐：原样写 str 值，不 trim——trim 会
    // 静默裁掉 prompt 的尾随换行，保存后草稿与生效值产生伪差异（r1 观察①）。
    const value = source[key]
    if (value) execution[key] = value
    else delete execution[key]
  }
  if (Object.keys(execution).length === 0) delete node.execution
  else node.execution = execution
  if (source.tools.length > 0) node.tools = [...source.tools]
  else delete node.tools
  if (Object.keys(source.requires_labels).length > 0) {
    node.requires_labels = { ...source.requires_labels }
  } else {
    delete node.requires_labels
  }
}

/**
 * 合并 YAML：结构取自 structureYaml，runtime 切片取自 runtimeYaml
 * （execution.runtime 保留结构侧——它是结构键）。runtime 侧空值 = 清除
 * （与后端 apply_runtime_config 的「完整切片替换」语义一致）；结构侧有而
 * runtime 侧没有的节点保持原值（回滚目标不认识的新节点保留现值）。
 * 运行配置保存请求体 = combineYaml(active 基线, 草稿)；
 * agent 建议「存为草稿」= combineYaml(建议, 当前草稿)。
 */
export function combineYamlStructureRuntime(
  structureYaml: string,
  runtimeYaml: string
): string {
  const structure = parseWorkflowYaml(structureYaml)
  const runtime = extractRuntimeSlice(runtimeYaml)
  for (const [key, node] of Object.entries(structure.nodes ?? {})) {
    const source = runtime.nodes[key]
    if (source) applyRuntimeToNode(node, source)
  }
  return dumpWorkflowYaml(structure)
}

/** 排序键的深比较（YAML mapping 键序不携带语义）。 */
function canonicalEqual(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return (
      a.length === b.length &&
      a.every((item, index) => canonicalEqual(item, b[index]))
    )
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const keysA = Object.keys(a).sort()
    const keysB = Object.keys(b).sort()
    return (
      keysA.length === keysB.length &&
      keysA.every((key, index) => key === keysB[index]) &&
      keysA.every((key) => canonicalEqual(a[key], b[key]))
    )
  }
  return a === b
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 结构载荷：剥掉 nodes-only runtime 切片后的定义——镜像后端
 * structural_payload。execution.runtime 保留为 profile_runtime；顶层
 * execution 不参与任何节点求值。 */
function structuralPayload(rawYaml: string): unknown {
  const doc = parseWorkflowYaml(rawYaml) as YamlWithTopExecution
  const payload = JSON.parse(JSON.stringify(doc)) as YamlWithTopExecution & {
    nodes?: Record<string, WorkflowYamlNode & { profile_runtime?: string }>
  }
  for (const node of Object.values(payload.nodes ?? {})) {
    node.profile_runtime = asConfigValue(node.execution?.runtime)
    delete node.execution
    delete node.tools
    delete node.requires_labels
  }
  delete payload.execution
  return payload
}

/** 变更键的用户可读格式化。旧历史里的 execution.<field> 仍显示为
 * 「顶层 <field>」供审计；新保存与回滚只产生 nodes.* 键。 */
export function formatRuntimeChangedKeys(keys: string[]): string {
  return keys
    .map((key) =>
      key.startsWith('nodes.')
        ? key.slice('nodes.'.length)
        : key.replace(/^execution\./, '顶层 ')
    )
    .join('、')
}

export type StructuralDiffSummary = {
  /** 结构载荷有差异的节点 key（含新增/删除），排序。 */
  nodeKeys: string[]
  edgesChanged: boolean
  /** nodes/edges 之外的顶层差异（label/intake/schema_version 等）。 */
  otherChanged: boolean
}

/** 两份 YAML 的结构差异汇总（agent 建议卡的「结构变更」组计数/列举用；
 * 解析失败按「整体有差异」保守返回）。 */
export function diffStructuralSummary(
  a: string,
  b: string
): StructuralDiffSummary {
  try {
    const pa = structuralPayload(a) as Record<string, unknown> & {
      nodes?: Record<string, unknown>
    }
    const pb = structuralPayload(b) as Record<string, unknown> & {
      nodes?: Record<string, unknown>
    }
    const nodesA = pa.nodes ?? {}
    const nodesB = pb.nodes ?? {}
    const nodeKeys = [
      ...new Set([...Object.keys(nodesA), ...Object.keys(nodesB)]),
    ]
      .filter((key) => !canonicalEqual(nodesA[key], nodesB[key]))
      .sort()
    const restA = { ...pa }
    const restB = { ...pb }
    delete restA.nodes
    delete restB.nodes
    delete restA.edges
    delete restB.edges
    return {
      nodeKeys,
      edgesChanged: !canonicalEqual(pa.edges ?? null, pb.edges ?? null),
      otherChanged: !canonicalEqual(restA, restB),
    }
  } catch {
    return { nodeKeys: [], edgesChanged: true, otherChanged: true }
  }
}

/** 两份 YAML 是否只差 runtime 切片（解析失败按有结构差异处理——调用方据此
 * 选择保守路径）。 */
export function hasStructuralYamlChanges(
  baselineYaml: string,
  draftYaml: string
): boolean {
  try {
    return !canonicalEqual(
      structuralPayload(baselineYaml),
      structuralPayload(draftYaml)
    )
  } catch {
    return true
  }
}
