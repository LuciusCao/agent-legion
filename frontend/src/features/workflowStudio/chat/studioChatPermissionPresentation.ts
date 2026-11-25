import type { ChatMessage, PermissionView } from './studioChatMessages'
import { permissionTurnKey } from './studioChatViewStability'

const NON_TERMINAL_KINDS = new Set(
  'read edit delete move search fetch think switch_mode'.split(' ')
)
const TERMINAL_TITLES = new Set(['bash', 'shell', 'terminal'])
const GRANT_OFF_NOTE = '终端授权围栏关闭时，不匹配或额外终端仍可执行。'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** kind 优先于标题；kimi 的 Bash 请求缺 kind 时才用已知标题兜底。 */
export function isTerminalPermission(toolTitle: string, kind: string): boolean {
  return kind
    ? !NON_TERMINAL_KINDS.has(kind)
    : TERMINAL_TITLES.has(toolTitle.trim().toLowerCase())
}

export function permissionSummaryText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map(permissionSummaryText).filter(Boolean).join('\n')
  }
  const record = asRecord(value)
  if (!record) return ''
  return asText(record.text) || permissionSummaryText(record.content)
}

export function decisionText(resolved: Record<string, unknown>): string {
  const decision = asRecord(resolved.decision)
  if (!decision) return '已处理'
  if (decision.deny === true) return '已拒绝'
  const via = asText(decision.via)
  if (via === 'auto_approved') return '已自动允许（平台工具）'
  if (via === 'auto_read_only') return '已自动允许（只读工具）'
  if (via === 'allow_all') return '已自动允许（本次全部允许）'
  return '已允许'
}

function approvedDecision(text: string | null): boolean {
  return text === '已允许' || text === '已自动允许（本次全部允许）'
}

export function firstPermissionCommands(
  messages: readonly ChatMessage[]
): Map<string, string> {
  const firstCommandBy = new Map<string, string>()
  const sortedMessages = [...messages].sort((a, b) => a.seq - b.seq)
  for (const [index, message] of sortedMessages.entries()) {
    if (message.kind !== 'tool_call') continue
    const content = asRecord(message.content)
    const toolCallId = asText(content?.toolCallId)
    const turnKey = permissionTurnKey(toolCallId, sortedMessages, index)
    if (!toolCallId || firstCommandBy.has(turnKey)) continue
    const command = asText(asRecord(content?.rawInput)?.command).trim()
    if (command) firstCommandBy.set(turnKey, command)
  }
  return firstCommandBy
}

export function permissionTerminalView(
  toolCall: Record<string, unknown> | null,
  firstCommand: string | undefined,
  resolved: Record<string, unknown> | undefined,
  toolTitle: string
) {
  const terminal = isTerminalPermission(toolTitle, asText(toolCall?.kind))
  const command = asText(asRecord(toolCall?.rawInput)?.command).trim() || null
  const approved = resolved ? approvedDecision(decisionText(resolved)) : false
  return {
    command,
    terminal,
    summary: terminal ? permissionSummaryText(toolCall?.content) || null : null,
    lateBoundCommand:
      terminal && !command && (!resolved || approved)
        ? firstCommand || null
        : null,
    decisionText: resolved ? decisionText(resolved) : null,
  }
}

/** 没有 pending 前置消息的自动批准仍渲染成一行系统提示。 */
export function permissionResolutionText(message: ChatMessage): string | null {
  const content = asRecord(message.content)
  if (content?.status !== 'resolved') return null
  const toolCall = asRecord(content.tool_call)
  const title = asText(toolCall?.title)
  const text = decisionText(content)
  return title ? `${text}：${title}` : text
}

export function permissionActions(
  permission: PermissionView,
  unbound: boolean
) {
  const allowOptions = permission.options.filter((option) =>
    option.kind.startsWith('allow')
  )
  const buttons = allowOptions.length > 0 ? allowOptions : permission.options
  return [
    ...buttons.map((option) => ({
      ...option,
      name: `${option.name}${unbound ? '（未绑定）' : ''}`,
      deny: false,
    })),
    { optionId: '', name: '拒绝', kind: 'deny', deny: true },
  ]
}

/** 仅派生展示：command 是批准时快照，late 不回写批准前可见性。 */
export function permissionPresentation(permission: PermissionView) {
  const pending = !permission.resolved
  const approved = approvedDecision(permission.decisionText)
  const late = approved ? permission.lateBoundCommand : null
  const command =
    permission.command ?? (pending ? permission.lateBoundCommand : null)
  const unbound =
    permission.terminal && !command && !late && (pending || approved)
  const note = pending
    ? command
      ? `你批准的就是上方完整命令；终端授权围栏开启时不允许追加、删改或替换为另一条命令。${GRANT_OFF_NOTE}`
      : `批准时无法核对完整命令。终端授权围栏开启时，主 Agent 批准后晚绑定首条完整命令；子 Agent 未上报事件时，一次批准仅对应一次终端执行，不限定命令。${GRANT_OFF_NOTE}`
    : late
      ? `完整命令在批准后才出现；终端授权围栏开启时晚绑定后不可替换，批准前只看到摘要。${GRANT_OFF_NOTE}`
      : unbound
        ? `没有可展示的完整命令；终端授权围栏开启时一次批准仅对应一次终端执行，不能推断实际命令。${GRANT_OFF_NOTE}`
        : ''
  const state = late
    ? '批准后已晚绑定'
    : unbound
      ? pending
        ? '命令未绑定 · 仅显示截断摘要'
        : '命令仍未绑定'
      : permission.terminal && command
        ? '命令已精确绑定'
        : ''
  const label = command
    ? !permission.terminal
      ? '将要运行的命令'
      : pending
        ? '完整命令 · 仅在授权围栏开启时精确绑定'
        : '批准时所见 · 完整命令'
    : permission.summary
      ? pending
        ? '权限请求携带的截断摘要 · 不是完整命令'
        : '批准时所见 · 截断摘要'
      : ''
  const details = [
    label,
    command ?? (permission.terminal ? permission.summary : ''),
    permission.terminal && !late ? note : '',
  ]
    .filter(Boolean)
    .join('\n\n')
  return {
    pending,
    unbound,
    state,
    details,
    lateDetails: late
      ? `批准后取得 · 仅在授权围栏开启时绑定的完整命令\n${late}\n\n${note}`
      : '',
    summary: !command && permission.terminal,
    ariaLabel: label || '终端授权说明',
  }
}
