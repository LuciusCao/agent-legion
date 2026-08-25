import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { createMaterialBundle } from '../api/materialsApi'
import { uploadMaterialFile } from '../lib/addItems'
import {
  MAX_BUNDLE_MEMBERS,
  parseBundleFolder,
  type BundleEntry,
  type BundleFileEntry,
  type BundleFileStatus,
} from '../lib/bundleFolder'

const UPLOAD_CONCURRENCY = 4

type FileRecord = {
  bundleKey: string
  path: string
  status: BundleFileStatus
  materialId: string | null
}

/**
 * Upload machinery for the bundle item type (AddItemsDialog 文件夹打包 tab,
 * materials-and-runs design §5, #156): each picked folder uploads its member
 * files through the regular material upload pump, then one create call
 * freezes the manifest (member material ids + relative paths). The bundle
 * counts as ONE run item.
 */
export function useBundleUploads(workspaceId: string | undefined) {
  const [bundles, setBundles] = useState<BundleEntry[]>([])

  const filesRef = useRef(new Map<string, { file: File }>())
  const fileStateRef = useRef(new Map<string, FileRecord>())
  const bundleNameRef = useRef(new Map<string, string>())
  const queueRef = useRef<{ bundleKey: string; fileKey: string }[]>([])
  const activeRef = useRef(0)
  const keySeqRef = useRef(0)
  // 防止重复创建：进入 creating/ready 的 bundle 打标，创建失败后移除。
  const createRequestedRef = useRef(new Set<string>())

  const updateBundle = useCallback(
    (bundleKey: string, patch: Partial<BundleEntry>) => {
      setBundles((prev) =>
        prev.map((bundle) =>
          bundle.key === bundleKey ? { ...bundle, ...patch } : bundle
        )
      )
    },
    []
  )

  const updateFile = useCallback(
    (bundleKey: string, fileKey: string, patch: Partial<BundleFileEntry>) => {
      setBundles((prev) =>
        prev.map((bundle) =>
          bundle.key === bundleKey
            ? {
                ...bundle,
                files: bundle.files.map((entry) =>
                  entry.key === fileKey ? { ...entry, ...patch } : entry
                ),
              }
            : bundle
        )
      )
    },
    []
  )

  // 文件全部落定后收尾：有失败标 failed，全部 done 则创建 bundle manifest。
  const finalizeBundle = useCallback(
    (bundleKey: string) => {
      if (createRequestedRef.current.has(bundleKey)) return
      const records = Array.from(fileStateRef.current.values()).filter(
        (record) => record.bundleKey === bundleKey
      )
      if (records.length === 0) return
      if (
        records.some(
          (record) =>
            record.status === 'pending' || record.status === 'uploading'
        )
      ) {
        updateBundle(bundleKey, { status: 'uploading', error: null })
        return
      }
      const failedCount = records.filter(
        (record) => record.status === 'failed'
      ).length
      if (failedCount > 0) {
        updateBundle(bundleKey, {
          status: 'failed',
          error: `${failedCount} 个文件上传失败`,
        })
        return
      }
      if (!workspaceId) return
      createRequestedRef.current.add(bundleKey)
      updateBundle(bundleKey, { status: 'creating', error: null })
      const members = records
        .map((record) => ({
          material_id: record.materialId!,
          path: record.path,
        }))
        .sort((a, b) => a.path.localeCompare(b.path))
      const name = bundleNameRef.current.get(bundleKey) ?? 'bundle'
      void createMaterialBundle(workspaceId, { name, members })
        .then((response) => {
          updateBundle(bundleKey, {
            status: 'ready',
            bundleId: response.bundle.id,
            error: null,
          })
        })
        .catch((err: unknown) => {
          createRequestedRef.current.delete(bundleKey)
          updateBundle(bundleKey, {
            status: 'failed',
            error: err instanceof Error ? err.message : '打包失败',
          })
        })
    },
    [workspaceId, updateBundle]
  )

  const pumpRef = useRef<() => void>(() => {})
  const finalizeRef = useRef<(bundleKey: string) => void>(() => {})
  const pump = useCallback(() => {
    while (activeRef.current < UPLOAD_CONCURRENCY && queueRef.current.length) {
      const item = queueRef.current.shift()!
      const record = filesRef.current.get(item.fileKey)
      if (!record || !workspaceId) continue
      activeRef.current += 1
      updateFile(item.bundleKey, item.fileKey, {
        status: 'uploading',
        error: null,
      })
      const state = fileStateRef.current.get(item.fileKey)
      if (state) {
        fileStateRef.current.set(item.fileKey, {
          ...state,
          status: 'uploading',
        })
      }
      void uploadMaterialFile(workspaceId, record.file, record.file.name)
        .then((result) => {
          const current = fileStateRef.current.get(item.fileKey)
          if (current) {
            fileStateRef.current.set(item.fileKey, {
              ...current,
              status: 'done',
              materialId: result.materialId,
            })
          }
          updateFile(item.bundleKey, item.fileKey, {
            status: 'done',
            materialId: result.materialId,
          })
        })
        .catch((err: unknown) => {
          const current = fileStateRef.current.get(item.fileKey)
          if (current) {
            fileStateRef.current.set(item.fileKey, {
              ...current,
              status: 'failed',
            })
          }
          updateFile(item.bundleKey, item.fileKey, {
            status: 'failed',
            error: err instanceof Error ? err.message : '上传失败',
          })
        })
        .finally(() => {
          activeRef.current -= 1
          pumpRef.current()
          finalizeRef.current(item.bundleKey)
        })
    }
  }, [workspaceId, updateFile])
  useEffect(() => {
    pumpRef.current = pump
  }, [pump])
  useEffect(() => {
    finalizeRef.current = finalizeBundle
  }, [finalizeBundle])

  const addFolder = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const bundleKey = `b${++keySeqRef.current}`
      const { root, drafts } = parseBundleFolder(fileList)
      const name = root || drafts[0]?.memberPath || 'bundle'
      if (drafts.length > MAX_BUNDLE_MEMBERS) {
        // 超限文件夹直接落成失败条目，不上传任何成员。
        setBundles((prev) => [
          ...prev,
          {
            key: bundleKey,
            name,
            files: [],
            status: 'failed',
            bundleId: null,
            error: `文件夹包含 ${drafts.length} 个文件，超过 ${MAX_BUNDLE_MEMBERS} 个成员上限`,
          },
        ])
        return
      }
      const fileEntries: BundleFileEntry[] = []
      for (const draft of drafts) {
        const fileKey = `bf${++keySeqRef.current}`
        filesRef.current.set(fileKey, { file: draft.file })
        fileStateRef.current.set(fileKey, {
          bundleKey,
          path: draft.memberPath,
          status: 'pending',
          materialId: null,
        })
        queueRef.current.push({ bundleKey, fileKey })
        fileEntries.push({
          key: fileKey,
          path: draft.memberPath,
          size: draft.size,
          status: 'pending',
          error: null,
          materialId: null,
        })
      }
      bundleNameRef.current.set(bundleKey, name)
      setBundles((prev) => [
        ...prev,
        {
          key: bundleKey,
          name,
          files: fileEntries,
          status: 'uploading',
          bundleId: null,
          error: null,
        },
      ])
      pump()
    },
    [pump]
  )

  const retryBundle = useCallback(
    (bundleKey: string) => {
      let requeued = 0
      for (const [fileKey, record] of fileStateRef.current) {
        if (record.bundleKey === bundleKey && record.status === 'failed') {
          fileStateRef.current.set(fileKey, { ...record, status: 'pending' })
          queueRef.current.push({ bundleKey, fileKey })
          requeued += 1
          updateFile(bundleKey, fileKey, { status: 'pending', error: null })
        }
      }
      if (requeued > 0) {
        updateBundle(bundleKey, { status: 'uploading', error: null })
        pumpRef.current()
      } else {
        // 文件全部成功但创建失败：直接重试创建。
        finalizeRef.current(bundleKey)
      }
    },
    [updateBundle, updateFile]
  )

  const removeBundle = useCallback((bundleKey: string) => {
    for (const [fileKey, record] of fileStateRef.current) {
      if (record.bundleKey === bundleKey) {
        fileStateRef.current.delete(fileKey)
        filesRef.current.delete(fileKey)
      }
    }
    queueRef.current = queueRef.current.filter(
      (item) => item.bundleKey !== bundleKey
    )
    createRequestedRef.current.delete(bundleKey)
    bundleNameRef.current.delete(bundleKey)
    setBundles((prev) => prev.filter((bundle) => bundle.key !== bundleKey))
  }, [])

  const resetBundles = useCallback(() => {
    setBundles([])
    filesRef.current.clear()
    fileStateRef.current.clear()
    bundleNameRef.current.clear()
    queueRef.current = []
    createRequestedRef.current.clear()
  }, [])

  const readyBundles = useMemo(
    () =>
      bundles.filter((bundle) => bundle.status === 'ready' && bundle.bundleId),
    [bundles]
  )
  const hasActiveBundles = bundles.some(
    (bundle) => bundle.status === 'uploading' || bundle.status === 'creating'
  )

  return {
    bundles,
    readyBundles,
    hasActiveBundles,
    addFolder,
    retryBundle,
    removeBundle,
    resetBundles,
  }
}
