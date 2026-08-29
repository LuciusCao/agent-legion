import { useState } from 'react'
import type { NodeDetailPreviewKind } from './nodeDetailPreviewContext'

const PREVIEW_KIND_LABELS: Record<NodeDetailPreviewKind, string> = {
  prompt: 'Prompt',
  skill: '技能文件',
}

/** 节点详情 panel 的预览状态（运行 Prompt / 技能文件）：带 nodeKey 印记，
 * 切换选中节点即自然失效，无需 effect 重置；面包屑后缀与分级返回都读它。 */
export function useNodeDetailPreview(nodeKey: string) {
  const [preview, setPreview] = useState<{
    nodeKey: string
    kind: NodeDetailPreviewKind
  } | null>(null)
  const activeKind = preview?.nodeKey === nodeKey ? preview.kind : null
  return {
    activeKind,
    activeLabel: activeKind ? PREVIEW_KIND_LABELS[activeKind] : null,
    showPreview: (kind: NodeDetailPreviewKind) => setPreview({ nodeKey, kind }),
    closePreview: () => setPreview(null),
  }
}
