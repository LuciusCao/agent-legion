/**
 * 预览面板查询 hooks（issue #328）。query key 留在本特性目录内定义
 * （previewPanel 是 #328 的自包含特性面，不扩散到 lib/queryKeys）。
 */
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

/**
 * 治理面状态（published + draft）。customizing（「定制预览」对话开着）时
 * 轮询：agent 经 MCP 写草稿后左栏/对话框内预览「改一版看一版」（仅当前
 * 用户可见——草稿渲染是本页面的客户端状态，不落任何共享通道）。
 */
export function usePreviewPanelState(
  workspaceId: string | undefined,
  customizing: boolean
) {
  return useQuery({
    queryKey: previewPanelKeys.state(workspaceId ?? ''),
    queryFn: () => fetchPreviewPanelState(workspaceId!),
    enabled: Boolean(workspaceId) && customizing,
    refetchInterval: customizing ? 3000 : false,
  })
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
    mutationFn: () => publishPreviewPanel(workspaceId!),
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
