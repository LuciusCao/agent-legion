// 排查会话的上下文引导消息与动作建议解析（纯函数，便于测试）。
//
// 上下文绑定模式（#329）：面板打开时自动把 workspace + job + node 作为首条
// 消息发给 agent（用户无需手工复制），agent 先用 get_job_context 拉会话绑定
// 的实时上下文。动作面分级：agent 只能输出「建议动作」payload（下方约定的
// fenced json 块），UI 渲染确认卡片，人确认后由宿主会话调 jobApi 执行。

import {
  textContent,
  type ChatMessage,
} from '../workflowStudio/chat/studioChatMessages'

export type JobDiagnosisTarget = {
  workspaceId: string
  jobId: string
  jobTitle?: string
  nodeKey?: string | null
  nodeLabel?: string | null
}

export type JobActionSuggestion = {
  action: 'rerun_node' | 'run_to_node'
  jobId: string
  nodeKey: string
  reason: string
}

/** 建议动作块的传输约定：agent 回复末尾的 ```json 围栏块。 */
const FENCED_JSON_RE = /```json\s*\n([\s\S]*?)```/g
const SUGGESTION_KEY = 'job_action_suggestion'
const KNOWN_ACTIONS = new Set<JobActionSuggestion['action']>([
  'rerun_node',
  'run_to_node',
])

export function buildDiagnosisPrimer(target: JobDiagnosisTarget): string {
  const lines = [
    '[排查上下文] 这是一次 job 排查会话（不是 workflow 创作任务）。',
    `- workspace_id: ${target.workspaceId}（本会话已绑定此 workspace）`,
    `- job_id: ${target.jobId}${target.jobTitle ? `（${target.jobTitle}）` : ''}`,
  ]
  if (target.nodeKey) {
    lines.push(
      `- 关注节点: ${target.nodeKey}${target.nodeLabel ? `（${target.nodeLabel}）` : ''}`
    )
  }
  lines.push(
    '',
    '除创作工具外，本 MCP server 还有一组 job 观测工具（全部只读）：',
    '- get_job_context(job_id, node_key?)：会话绑定的 job 上下文，先调它',
    '- get_job_detail(workspace_id, job_id)：节点状态、run 列表、产物清单、建议动作',
    '- get_node_logs(workspace_id, job_id, node_key?/run_id?)：节点日志（默认取最近失败的 run）',
    '- read_artifact(workspace_id, job_id, artifact_name)：读产物内容（上游输入同理）',
    '- list_jobs(workspace_id, status?, limit?)：最近的 job 列表',
    '- compare_jobs(workspace_id, job_id_a, job_id_b)：对比两个 job（如 vs 最近成功的一次）',
    '',
    '排查路径建议：job 详情与节点状态 → 失败节点日志/错误 → 上游输入产物 → 节点代码与 prompt → 必要时历史对比。',
    '',
    '你只能读取与分析，重跑/修改一律由人确认后执行。给出处置建议时，在回复末尾附上动作建议块（```json 围栏）：',
    `{"${SUGGESTION_KEY}": {"action": "rerun_node", "job_id": "${target.jobId}", "node_key": "<节点>", "reason": "<一句话理由>"}}`,
    '（action 可选 rerun_node=从该节点重跑 / run_to_node=重跑至该节点。）UI 会把它渲染成确认卡片。先给诊断结论，再给建议。'
  )
  return lines.join('\n')
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** 解析一段 agent 文本里的全部合法动作建议（围栏 json + 固定 envelope）。 */
export function parseJobActionSuggestions(text: string): JobActionSuggestion[] {
  const suggestions: JobActionSuggestion[] = []
  for (const match of text.matchAll(FENCED_JSON_RE)) {
    let parsed: unknown
    try {
      parsed = JSON.parse(match[1])
    } catch {
      continue
    }
    const envelope = asRecord(asRecord(parsed)?.[SUGGESTION_KEY])
    if (!envelope) continue
    const action = typeof envelope.action === 'string' ? envelope.action : ''
    const jobId = typeof envelope.job_id === 'string' ? envelope.job_id : ''
    const nodeKey =
      typeof envelope.node_key === 'string' ? envelope.node_key : ''
    if (!KNOWN_ACTIONS.has(action as JobActionSuggestion['action'])) continue
    if (!jobId || !nodeKey) continue
    suggestions.push({
      action: action as JobActionSuggestion['action'],
      jobId,
      nodeKey,
      reason: typeof envelope.reason === 'string' ? envelope.reason.trim() : '',
    })
  }
  return suggestions
}

/** 取最后一条携带合法建议的 agent 消息里的建议（旧消息里的建议随对话推进
 * 失效）；只接受针对当前绑定 job 的建议，杜绝 agent 误指其他 job。 */
export function latestJobActionSuggestions(
  messages: ChatMessage[],
  jobId: string
): JobActionSuggestion[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message.kind !== 'text' || message.role !== 'agent') continue
    const found = parseJobActionSuggestions(textContent(message)).filter(
      (suggestion) => suggestion.jobId === jobId
    )
    if (found.length > 0) return found
  }
  return []
}

export function suggestionKey(suggestion: JobActionSuggestion): string {
  return `${suggestion.action}:${suggestion.jobId}:${suggestion.nodeKey}`
}

export function suggestionTitle(suggestion: JobActionSuggestion): string {
  return suggestion.action === 'rerun_node'
    ? `重跑节点 ${suggestion.nodeKey}`
    : `重跑至节点 ${suggestion.nodeKey}`
}
