import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { StudioAgentRegistryResponse } from '../../api/studioAgents'

// #355：PUT 409（陈旧快照冲突）的确认对话框与处理原语。从
// StudioAgentsSectionParts 拆出（文件预算）：冲突流程的展示与状态全部
// 收在这里，编辑器主文件只持有「capture → 弹窗 → applyResult」三个触点。

/**
 * 从 PUT 失败 error 中取出 409 携带的当前注册表（#719 后 httpError 只保留
 * detail，冲突文档经 detail 传递）。非 409 或 detail 不是注册表文档（代理
 * 剥离响应体等异常路径）返回 null——调用方降级为普通错误提示，不能卡在
 * 无提示态。api_base 必须是字符串：缺该键的残缺 detail 若被采纳会把
 * undefined 写进 apiBase state（复验 P3）。
 */
export function registryConflictBody(
  error: unknown
): StudioAgentRegistryResponse | null {
  const { status, detail } = (error ?? {}) as {
    status?: number
    detail?: unknown
  }
  if (status !== 409 || !detail || typeof detail !== 'object') return null
  if (!('agents' in detail) || !('revision' in detail)) return null
  if (typeof (detail as { api_base?: unknown }).api_base !== 'string') {
    return null
  }
  return detail as StudioAgentRegistryResponse
}

/**
 * 409 冲突状态机：capture() 识别冲突错误并持有 409 携带的最新文档；dialog
 * 渲染确认框。「刷新注册表」直接 apply 该文档（与存储同事务产出），一次性
 * 前进编辑器（含 revision），不自动重试、不重新拉取。
 */
export function useConflictRefresh(
  applyResult: (result: StudioAgentRegistryResponse) => void
) {
  const [conflictBody, setConflictBody] =
    useState<StudioAgentRegistryResponse | null>(null)

  function capture(error: unknown): boolean {
    const body = registryConflictBody(error)
    if (body) setConflictBody(body)
    return body !== null
  }

  const dialog = (
    <ConflictRefreshDialog
      open={conflictBody !== null}
      onContinue={() => setConflictBody(null)}
      onRefresh={() => {
        if (conflictBody) applyResult(conflictBody)
        setConflictBody(null)
      }}
    />
  )
  return { capture, dialog }
}

/** 409 冲突确认对话框（纯展示；刷新动作由父级执行 applyResult）。 */
export function ConflictRefreshDialog(props: {
  open: boolean
  onContinue: () => void
  onRefresh: () => void
}) {
  return (
    <Dialog
      open={props.open}
      onClose={props.onContinue}
      aria-labelledby="studio-agents-conflict-title"
    >
      <DialogTitle id="studio-agents-conflict-title">
        注册表已被其他修改更新
      </DialogTitle>
      <DialogContent>
        保存期间注册表被其他修改更新（例如自动探测合并了新 agent），
        为避免覆盖丢失条目，请刷新后基于最新内容重试。刷新将丢弃当前
        未保存的编辑。
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={props.onContinue}>
          继续编辑
        </Button>
        <Button variant="contained" onClick={props.onRefresh}>
          刷新注册表
        </Button>
      </DialogActions>
    </Dialog>
  )
}
