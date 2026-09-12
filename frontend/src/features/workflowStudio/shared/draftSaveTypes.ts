import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'

/** 草稿保存的类型与常量（#633 从 draftSaveController.ts 拆出，文件体积
 * 预算）：状态形状、flush 终态、PUT 函数契约与 debounce/重试/keepalive
 * 参数。 */

export type DraftSaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error'
export type DraftSaveState = {
  status: DraftSaveStatus
  savedAt: string | null
  /** GET 草稿查询失败（仅内存模式）时由组合层合并进来，供 UI 警示。 */
  loadError?: boolean
  /** #633：CAS 冲突——服务端草稿已被 agent/其它会话推进，本页未保存的
   * 编辑没有落盘。与 error（网络失败，自动重试）不同：冲突不会自动重试，
   * 需要用户决定（采用服务端草稿或强制覆盖）。 */
  conflict?: boolean
  /** 冲突时服务端当前的草稿（采用/查看用；解析失败时为 null）。 */
  conflictDraftYaml?: string | null
}

export const IDLE_DRAFT_SAVE: DraftSaveState = { status: 'idle', savedAt: null }

export const DEBOUNCE_MS = 800
export const MAX_PUT_RETRIES = 2
export const RETRY_BASE_MS = 2000
// fetch keepalive 请求体上限约 64KiB（按 UTF-8 字节计），留安全余量；超出
// 退化为普通 PUT（pagehide 下尽力而为，不再享受 keepalive 的存活保证）。
const KEEPALIVE_MAX_BODY_BYTES = 60_000

export type PutWorkflowDraftFn = (
  yaml: string,
  keepalive: boolean,
  expectedUpdatedAt: string | null
) => Promise<WorkflowDraftStoreResponse>

/** #429：flushNow 的结果——本次 flush 是否把最新内容落到了服务端。
 * controller 全路径 resolve 不 reject（失败态进 state），调用方（agent 发布
 * 确认）必须在 await 之后读这个终态，不能读 React useState 快照的
 * studio.draftSave——闭包捕获的是点击那一刻的 state，await 期间 PUT 失败
 * 落定的 error 态快照链路看不见，守卫会漏过并发布旧草稿。 */
export type DraftSaveFlushResult = {
  /** true：本次 flush 的 PUT 成功（最新内容已落盘）。 */
  ok: boolean
  /** 落定后的 controller 终态（live 引用，非 React 快照）。 */
  state: DraftSaveState
}

// yaml.length 是 UTF-16 码元数，中文草稿会严重低估体积；必须对完整 JSON
// 请求体按 UTF-8 字节数判断。
export function withinKeepaliveLimit(yaml: string): boolean {
  const body = JSON.stringify({ definition_yaml: yaml })
  return new TextEncoder().encode(body).byteLength <= KEEPALIVE_MAX_BODY_BYTES
}
