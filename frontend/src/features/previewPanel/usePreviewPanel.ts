/**
 * 预览面板查询 hooks（issue #328）。query key 留在本特性目录内定义
 * （previewPanel 是 #328 的自包含特性面，不扩散到 lib/queryKeys）。
 */
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  archivePreviewPanel,
  fetchPreviewPanelState,
  fetchPublishedPreviewPanel,
  publishPreviewPanel,
} from './previewPanelApi'

const previewPanelKeys = {
  published: (workspaceId: string) =>
    ['preview-panel', workspaceId, 'published'] as const,
  state: (workspaceId: string) =>
    ['preview-panel', workspaceId, 'state'] as const,
}

/** 已发布 bundle（左栏渲染依据）；无定制时 data 为 null（回落通用预览）。 */
export function usePublishedPreviewPanel(workspaceId: string | undefined) {
  return useQuery({
    queryKey: previewPanelKeys.published(workspaceId ?? ''),
    queryFn: () => fetchPublishedPreviewPanel(workspaceId!),
    enabled: Boolean(workspaceId),
  })
}

/** 定制对话开着（agent 正在写草稿）时的轮询间隔：「改一版看一版」要跟手。 */
export const PREVIEW_STATE_ACTIVE_POLL_MS = 3_000
/** 对话关着时的轮询间隔（#965）：只为头部治理行的草稿状态，不需要跟手。 */
export const PREVIEW_STATE_IDLE_POLL_MS = 30_000

/** 治理面状态轮询档位（#965）：未启用不轮询；定制对话开着 3s，否则 30s。 */
export function previewPanelStatePollInterval(
  enabled: boolean,
  customizing: boolean
): number | false {
  if (!enabled) return false
  return customizing ? PREVIEW_STATE_ACTIVE_POLL_MS : PREVIEW_STATE_IDLE_POLL_MS
}

/**
 * 治理面状态（published + draft）。enabled 时轮询：agent 经 MCP 写草稿后
 * 左栏预览「改一版看一版」（仅当前用户可见——草稿渲染是本页面的客户端
 * 状态，不落任何共享通道）。#796 返工后由调用方按 admin 身份常驻开启
 * （头部治理行需要草稿状态，原来只在「定制预览」面板开着时启用）。
 * #965：常驻 3s 与 job 详情轮询叠加过密——只有定制对话开着（agent 在写
 * 草稿）时保持 3s，关着降到 30s；发布/归档成功后的 invalidate 不受影响。
 */
export function usePreviewPanelState(
  workspaceId: string | undefined,
  enabled: boolean,
  customizing = false
) {
  const query = useQuery({
    queryKey: previewPanelKeys.state(workspaceId ?? ''),
    queryFn: ({ signal }) => fetchPreviewPanelState(workspaceId!, signal),
    enabled: Boolean(workspaceId) && enabled,
    refetchInterval: previewPanelStatePollInterval(enabled, customizing),
  })
  // 对话打开即刷新一次：空闲档最多 30s 前的帧不该成为「改一版看一版」的起点。
  const { refetch } = query
  const active = Boolean(workspaceId) && enabled && customizing
  useEffect(() => {
    if (active) void refetch()
  }, [active, refetch])
  return query
}

function useInvalidatePreviewPanel(workspaceId: string | undefined) {
  const queryClient = useQueryClient()
  return () => {
    if (!workspaceId) return
    void queryClient.invalidateQueries({
      queryKey: previewPanelKeys.published(workspaceId),
    })
    void queryClient.invalidateQueries({
      queryKey: previewPanelKeys.state(workspaceId),
    })
  }
}

export function usePublishPreviewPanel(workspaceId: string | undefined) {
  const invalidate = useInvalidatePreviewPanel(workspaceId)
  return useMutation({
    mutationFn: (expectedHash: string) =>
      publishPreviewPanel(workspaceId!, expectedHash),
    onSuccess: invalidate,
  })
}

export function useArchivePreviewPanel(workspaceId: string | undefined) {
  const invalidate = useInvalidatePreviewPanel(workspaceId)
  return useMutation({
    mutationFn: () => archivePreviewPanel(workspaceId!),
    onSuccess: invalidate,
  })
}
