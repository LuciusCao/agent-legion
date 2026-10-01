/**
 * 预览显示模式（issue #528）：已发布定制面板与原始界面（question 内置
 * bundle / 通用产物预览）的客户端查看偏好。纯客户端状态——不写服务端、
 * 不影响其他用户；按 workspace 存 localStorage（避免全局开关误伤多
 * workspace 用户），默认 `custom`（现状：定制面板优先）。损坏值/读不到
 * 一律回退默认。
 */
export type PreviewDisplayMode = 'custom' | 'original'

/** 会话内手动切换的覆盖（只认同一 workspace，防跨 workspace 串扰）。 */
export interface PreviewDisplayModeOverride {
  workspaceId: string
  mode: PreviewDisplayMode
}

/** 解析当前生效模式：会话内覆盖（同 workspace 才认）> 存储偏好 > 默认。 */
export function resolvePreviewDisplayMode(
  workspaceId: string | undefined,
  override: PreviewDisplayModeOverride | null
): PreviewDisplayMode {
  if (workspaceId === undefined) return 'custom'
  if (override && override.workspaceId === workspaceId) return override.mode
  return loadPreviewDisplayMode(workspaceId)
}

const STORAGE_PREFIX = 'preview-panel-display-mode:'

export function loadPreviewDisplayMode(
  workspaceId: string
): PreviewDisplayMode {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + workspaceId)
    return raw === 'original' ? 'original' : 'custom'
  } catch {
    // 隐私模式等读失败：回退默认（不记忆 ≠ 功能不可用）。
    return 'custom'
  }
}

export function savePreviewDisplayMode(
  workspaceId: string,
  mode: PreviewDisplayMode
): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + workspaceId, mode)
  } catch {
    // 写入失败（配额/隐私模式）：降级为本次会话内记忆，不打断交互。
  }
}
