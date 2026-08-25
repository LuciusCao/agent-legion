import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fileTypeGroup, uploadMaterialFile } from '../lib/addItems'

export type UploadStatus = 'pending' | 'uploading' | 'done' | 'failed'

export type UploadEntry = {
  key: string
  name: string
  size: number
  group: string
  status: UploadStatus
  error: string | null
  materialId: string | null
  deduplicated: boolean
}

export const STATUS_LABELS: Record<UploadStatus, string> = {
  pending: '待传',
  uploading: '上传中',
  done: '完成',
  failed: '失败',
}

const UPLOAD_CONCURRENCY = 4

/**
 * Upload machinery for the material item type (AddItemsDialog 上传材料 tab):
 * file queue with bounded concurrency, per-file status, retry and removal.
 */
export function useMaterialUploads(workspaceId: string | undefined) {
  const [entries, setEntries] = useState<UploadEntry[]>([])

  const filesRef = useRef(new Map<string, { file: File; name: string }>())
  const queueRef = useRef<string[]>([])
  const activeRef = useRef(0)
  const keySeqRef = useRef(0)

  const updateEntry = useCallback(
    (key: string, patch: Partial<UploadEntry>) => {
      setEntries((prev) =>
        prev.map((entry) =>
          entry.key === key ? { ...entry, ...patch } : entry
        )
      )
    },
    []
  )

  const pumpRef = useRef<() => void>(() => {})
  const pump = useCallback(() => {
    while (activeRef.current < UPLOAD_CONCURRENCY && queueRef.current.length) {
      const key = queueRef.current.shift()!
      const record = filesRef.current.get(key)
      if (!record || !workspaceId) continue
      activeRef.current += 1
      updateEntry(key, { status: 'uploading', error: null })
      void uploadMaterialFile(workspaceId, record.file, record.name)
        .then((result) => {
          updateEntry(key, {
            status: 'done',
            materialId: result.materialId,
            deduplicated: result.deduplicated,
          })
        })
        .catch((err: unknown) => {
          updateEntry(key, {
            status: 'failed',
            error: err instanceof Error ? err.message : '上传失败',
          })
        })
        .finally(() => {
          activeRef.current -= 1
          pumpRef.current()
        })
    }
  }, [workspaceId, updateEntry])
  useEffect(() => {
    pumpRef.current = pump
  }, [pump])

  const addFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const next: UploadEntry[] = []
      for (const file of Array.from(fileList)) {
        const key = `f${++keySeqRef.current}`
        const name =
          (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
          file.name
        filesRef.current.set(key, { file, name })
        queueRef.current.push(key)
        next.push({
          key,
          name,
          size: file.size,
          group: fileTypeGroup(name, file.type),
          status: 'pending',
          error: null,
          materialId: null,
          deduplicated: false,
        })
      }
      setEntries((prev) => [...prev, ...next])
      pump()
    },
    [pump]
  )

  const retryEntry = useCallback(
    (key: string) => {
      updateEntry(key, { status: 'pending', error: null })
      queueRef.current.push(key)
      pump()
    },
    [pump, updateEntry]
  )

  const removeEntry = useCallback((key: string) => {
    filesRef.current.delete(key)
    setEntries((prev) => prev.filter((entry) => entry.key !== key))
  }, [])

  const resetUploads = useCallback(() => {
    setEntries([])
    filesRef.current.clear()
    queueRef.current = []
  }, [])

  const doneEntries = useMemo(
    () =>
      entries.filter((entry) => entry.status === 'done' && entry.materialId),
    [entries]
  )
  const hasActiveUploads = entries.some(
    (entry) => entry.status === 'pending' || entry.status === 'uploading'
  )

  return {
    entries,
    doneEntries,
    hasActiveUploads,
    addFiles,
    retryEntry,
    removeEntry,
    resetUploads,
  }
}
