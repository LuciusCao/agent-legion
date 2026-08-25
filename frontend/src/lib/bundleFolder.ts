import { splitBundleRelativePath } from './addItems'

export type BundleFileStatus = 'pending' | 'uploading' | 'done' | 'failed'

export type BundleFileEntry = {
  key: string
  path: string
  size: number
  status: BundleFileStatus
  error: string | null
  materialId: string | null
}

export type BundleStatus = 'uploading' | 'creating' | 'ready' | 'failed'

export type BundleEntry = {
  key: string
  name: string
  files: BundleFileEntry[]
  status: BundleStatus
  bundleId: string | null
  error: string | null
}

export const BUNDLE_STATUS_LABELS: Record<BundleStatus, string> = {
  uploading: '上传中',
  creating: '打包中',
  ready: '就绪',
  failed: '失败',
}

// 与后端 MAX_BUNDLE_MEMBERS（server/app/services/material_bundles.py）保持一致：
// 超限文件夹在上传前直接拒收，避免上传数 GB 后才被创建接口拒绝。
export const MAX_BUNDLE_MEMBERS = 1000

export type BundleFileDraft = {
  file: File
  memberPath: string
  size: number
}

/**
 * 解析 webkitdirectory 的 FileList：剥出公共根名，成员草稿保持选择顺序。
 */
export function parseBundleFolder(fileList: FileList): {
  root: string
  drafts: BundleFileDraft[]
} {
  let root = ''
  const drafts: BundleFileDraft[] = []
  for (const file of Array.from(fileList)) {
    const relativePath =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
      file.name
    const parts = splitBundleRelativePath(relativePath)
    if (!root && parts.root) root = parts.root
    drafts.push({ file, memberPath: parts.memberPath, size: file.size })
  }
  return { root, drafts }
}
