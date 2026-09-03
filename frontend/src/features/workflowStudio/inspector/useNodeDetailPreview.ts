import { useState } from 'react'
import type { NodeDetailPreviewKind } from './nodeDetailPreviewContext'

const PREVIEW_KIND_LABELS: Record<NodeDetailPreviewKind, string> = {
  prompt: 'Prompt',
  skill: '技能文件',
}

/** 节点详情 panel 的预览状态（运行 Prompt / 技能文件）：nodeKey 变化时
 * 在渲染期间清除（React 认可的 derive-state-from-props 重置，无需 effect），
 * 切走再切回也落在节点详情而非残留预览；面包屑后缀与分级返回都读它。 */
export function useNodeDetailPreview(nodeKey: string) {
  const [kind, setKind] = useState<NodeDetailPreviewKind | null>(null)
  const [prevNodeKey, setPrevNodeKey] = useState(nodeKey)
  if (prevNodeKey !== nodeKey) {
    setPrevNodeKey(nodeKey)
    setKind(null)
  }
  return {
    activeKind: kind,
    // 面包屑后缀（自带「 / 」分隔符），无预览时空串，调用方直接拼接。
    crumbs: kind ? ` / ${PREVIEW_KIND_LABELS[kind]}` : '',
    showPreview: (next: NodeDetailPreviewKind) => setKind(next),
    closePreview: () => setKind(null),
  }
}
