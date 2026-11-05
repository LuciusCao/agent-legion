import type { ServerDraftConflictPayload } from './serverDraftAdvance'

export type ServerDraftConflict = ServerDraftConflictPayload

export type ServerDraftApplyControls = {
  /* touched-aware setter：经它（用户编辑/聊天应用/重置/采用历史版本）的
     写入才算「用户碰过」；useDraftBaselineSync 的内部 reset 不算。 */
  setDraftYaml: (value: string) => void
  /* 服务端草稿前进但用户有本地编辑时挂起的冲突通知；由保存层消费成
     conflict 态。读取即消费（再次调用返回 null）。 */
  consumeConflict: () => ServerDraftConflict | null
}

export type ServerDraftApplyInput = {
  workspaceId: string | undefined
  originalYaml: string
  serverDraftYaml: string | null | undefined
  serverDraftUpdatedAt: string | null | undefined
  setDraftYamlState: (value: string) => void
  canvasYaml?: string
  serverDraftHash?: string | null
  serverDraftGeneration?: number | null
}

export type ServerDraftReapplyInput = Pick<
  ServerDraftApplyInput,
  | 'serverDraftYaml'
  | 'serverDraftUpdatedAt'
  | 'setDraftYamlState'
  | 'canvasYaml'
  | 'serverDraftHash'
  | 'serverDraftGeneration'
>
