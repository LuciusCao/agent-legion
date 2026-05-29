import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useDetailStore } from '../stores/detailStore'
import { useArtifactStore } from '../stores/artifactStore'
import { useInteractionStore } from '../stores/interactionStore'
import { useUiStore } from '../stores/uiStore'
import { useVideoStore } from '../stores/videoStore'
import { useVideoPhaseEvents } from './useVideoPhaseEvents'
import {
  binarySearchTriggerIndex,
  prepareIndexedTriggers,
} from '../lib/search'
import { api } from '../api'
import { parseTimeSeconds, triggerDownload } from '../helpers'
import type {
  VideoItem,
  PhaseRun,
  TranscriptionRun,
  VideoArtifacts,
  InteractionNode,
} from '../types'

type MoreDialogType = 'subtitles' | 'nodes' | 'metadata' | null

export interface UseDetailPageReturn {
  video: VideoItem | null
  isLoading: boolean
  playerRef: React.RefObject<HTMLVideoElement | null>
  currentTime: number
  moreDialogOpen: boolean
  moreDialogType: MoreDialogType
  runToDialogOpen: boolean
  phaseRuns: PhaseRun[]
  transcriptionRuns: TranscriptionRun[]
  artifacts: VideoArtifacts
  triggeredNodeIndexes: Set<number>
  dismissedNodeIndexes: Set<number>
  currentSentence: string[]
  activeNode: InteractionNode | null
  detailTitle: string
  isPlaying: boolean
  setIsPlaying: (v: boolean) => void
  handleTimeUpdate: (time: number) => void
  handleSeek: (time: number) => void
  handleContinue: () => void
  handleDeleteConfirm: () => Promise<boolean>
  handlePackage: () => Promise<void>
  handleRerun: (phase: string) => Promise<void>
  handleRunTo: (params: {
    targetPhase: string
    startPhase: string | null
  }) => Promise<void>
  openMoreDialog: (type: MoreDialogType) => void
  closeMoreDialog: () => void
  setRunToDialogOpen: (v: boolean) => void
  setMoreDialogOpen: (v: boolean) => void
  openRerunDialog: () => void
  openDeleteDialog: () => void
  pushWord: (word: string) => void
  resetSentence: () => void
  replayInteraction: (index: number) => void
}

export function useDetailPage(): UseDetailPageReturn {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const playerRef = useRef<HTMLVideoElement>(null)
  const previousPlaybackTimeRef = useRef<number | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [moreDialogOpen, setMoreDialogOpen] = useState(false)
  const [moreDialogType, setMoreDialogType] = useState<MoreDialogType>(null)
  const [runToDialogOpen, setRunToDialogOpen] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)

  const {
    currentVideo,
    phaseRuns,
    transcriptionRuns,
    loadVideo,
    loadLog,
    isLoading,
  } = useDetailStore()

  const { artifacts, loadArtifacts } = useArtifactStore()

  const {
    triggeredNodeIndexes,
    dismissedNodeIndexes,
    currentSentence,
    triggerInteraction,
    dismissInteraction,
    replayInteraction,
    pushWord,
    resetSentence,
    clearSentence,
    clearInteractions,
  } = useInteractionStore()

  const { openRerunDialog, openDeleteDialog, showToast } = useUiStore()
  const { fetchVideos } = useVideoStore()

  const indexedTriggers = useMemo(
    () => prepareIndexedTriggers(artifacts.interactions),
    [artifacts.interactions]
  )

  const checkFetchError = useCallback(() => {
    const err = useVideoStore.getState().error
    if (err) {
      showToast(`加载失败: ${err}`, 'error')
      useVideoStore.getState().clearError()
    }
  }, [showToast])

  useVideoPhaseEvents(id)

  useEffect(() => {
    if (!id) return
    clearInteractions()
    previousPlaybackTimeRef.current = null
    let stale = false
    const load = async () => {
      await Promise.all([loadVideo(id), loadArtifacts(id), loadLog(id)])
      if (stale) return
    }
    load()
    return () => {
      stale = true
    }
  }, [id, clearInteractions, loadVideo, loadArtifacts, loadLog])

  const prevPhaseRef = useRef<string | null>(null)
  const prevStatusRef = useRef<string | null>(null)

  useEffect(() => {
    if (!id || !currentVideo) return
    const currentPhase = currentVideo.current_phase
    const currentStatus = currentVideo.status
    if (
      prevPhaseRef.current !== null &&
      (prevPhaseRef.current !== currentPhase ||
        prevStatusRef.current !== currentStatus)
    ) {
      loadArtifacts(id)
      loadLog(id)
    }
    prevPhaseRef.current = currentPhase
    prevStatusRef.current = currentStatus
  }, [id, currentVideo, loadArtifacts, loadLog])

  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time)
      const previousTime = previousPlaybackTimeRef.current

      const idx = binarySearchTriggerIndex(time, indexedTriggers)
      if (idx === -1) {
        previousPlaybackTimeRef.current = time
        return
      }

      const node = artifacts.interactions[idx]
      const trigger = parseTimeSeconds(node.trigger_time ?? 0)
      if (!Number.isFinite(trigger)) {
        previousPlaybackTimeRef.current = time
        return
      }

      const crossedTrigger =
        previousTime !== null && previousTime < trigger && time >= trigger
      const reachedTriggerWindow = time >= trigger && time < trigger + 1.5
      if (
        !triggeredNodeIndexes.has(idx) &&
        !dismissedNodeIndexes.has(idx) &&
        isPlaying &&
        (crossedTrigger || reachedTriggerWindow)
      ) {
        triggerInteraction(idx)
      }
      previousPlaybackTimeRef.current = time
    },
    [
      indexedTriggers,
      artifacts.interactions,
      triggeredNodeIndexes,
      dismissedNodeIndexes,
      isPlaying,
      triggerInteraction,
    ]
  )

  const handleSeek = useCallback((time: number) => {
    if (playerRef.current) playerRef.current.currentTime = time
  }, [])

  const activeNodeIndex = Array.from(triggeredNodeIndexes).pop()
  const activeNode =
    activeNodeIndex !== undefined
      ? artifacts.interactions[activeNodeIndex]
      : null
  const detailTitle = currentVideo?.title || '未选择资源'

  const handleContinue = useCallback(() => {
    clearSentence()
    if (activeNodeIndex !== undefined) {
      dismissInteraction(activeNodeIndex)
    }
    playerRef.current?.play()
  }, [clearSentence, activeNodeIndex, dismissInteraction])

  const handleDeleteConfirm = useCallback(async () => {
    if (!id) return false
    try {
      await api(`/api/videos/${id}`, { method: 'DELETE' })
      showToast('删除成功', 'success')
      await fetchVideos()
      checkFetchError()
      navigate('/')
      return true
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      showToast(`删除失败: ${message}`, 'error')
      return false
    }
  }, [id, navigate, fetchVideos, showToast, checkFetchError])

  const handlePackage = useCallback(async () => {
    if (!id) return
    await api<{ accepted: boolean }>('/api/package', {
      method: 'POST',
      body: JSON.stringify({ video_ids: [id] }),
    })
    showToast('打包已提交，完成后将自动下载', 'success')
  }, [id, showToast])

  const handleRerun = useCallback(
    async (phase: string) => {
      if (!id) return
      try {
        await api(`/api/videos/${id}/rerun`, {
          method: 'POST',
          body: JSON.stringify({ phase }),
        })
        showToast('重跑已提交', 'success')
        await Promise.all([fetchVideos(), loadVideo(id), loadLog(id)])
        checkFetchError()
      } catch (err) {
        if (
          err instanceof Error &&
          err.message.includes('currently being processed')
        ) {
          showToast('该资源正在被处理中，请等待当前阶段完成后再重跑。', 'error')
        } else {
          const message = err instanceof Error ? err.message : String(err)
          showToast(`重跑失败: ${message}`, 'error')
        }
      }
    },
    [id, fetchVideos, loadVideo, loadLog, showToast, checkFetchError]
  )

  const handleRunTo = useCallback(
    async ({
      targetPhase,
      startPhase,
    }: {
      targetPhase: string
      startPhase: string | null
    }) => {
      if (!id) return
      try {
        await api(`/api/videos/${id}/run-to`, {
          method: 'POST',
          body: JSON.stringify({
            target_phase: targetPhase,
            start_phase: startPhase,
          }),
        })
        showToast(startPhase ? '重跑运行已提交' : '运行已提交', 'success')
        setRunToDialogOpen(false)
        await Promise.all([fetchVideos(), loadVideo(id), loadLog(id)])
        checkFetchError()
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        showToast(`运行失败: ${message}`, 'error')
      }
    },
    [id, fetchVideos, loadVideo, loadLog, showToast, checkFetchError]
  )

  const openMoreDialog = (type: MoreDialogType) => {
    setMoreDialogOpen(false)
    setMoreDialogType(type)
  }

  const closeMoreDialog = () => {
    setMoreDialogOpen(false)
    setMoreDialogType(null)
  }

  return {
    video: currentVideo,
    isLoading,
    playerRef,
    currentTime,
    moreDialogOpen,
    moreDialogType,
    runToDialogOpen,
    phaseRuns,
    transcriptionRuns,
    artifacts,
    triggeredNodeIndexes,
    dismissedNodeIndexes,
    currentSentence,
    activeNode,
    detailTitle,
    isPlaying,
    setIsPlaying,
    handleTimeUpdate,
    handleSeek,
    handleContinue,
    handleDeleteConfirm,
    handlePackage,
    handleRerun,
    handleRunTo,
    openMoreDialog,
    closeMoreDialog,
    setRunToDialogOpen,
    setMoreDialogOpen,
    openRerunDialog,
    openDeleteDialog,
    pushWord,
    resetSentence,
    replayInteraction,
  }
}
