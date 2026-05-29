import { useEffect, useRef } from 'react'
import { useDetailStore } from '../stores/detailStore'

export function useVideoPhaseEvents(videoId: string | undefined) {
  const updatePhaseRuns = useDetailStore((state) => state.updatePhaseRuns)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!videoId || typeof EventSource === 'undefined') return

    let source: EventSource | null = null
    let reconnectDelay = 1000
    const maxReconnectDelay = 30000

    const connect = () => {
      if (source) return
      source = new EventSource(`/api/videos/${videoId}/events`)

      source.onopen = () => {
        reconnectDelay = 1000
      }

      source.onmessage = (event) => {
        if (!event.data || event.data.startsWith(':heartbeat')) return
        try {
          const payload = JSON.parse(event.data)
          if (payload.type === 'phase_runs_updated' && payload.phase_runs) {
            updatePhaseRuns(
              payload.phase_runs,
              payload.transcription_runs || [],
              payload.video
            )
          }
        } catch {
          // ignore
        }
      }

      source.onerror = () => {
        if (source) {
          source.close()
          source = null
        }
        reconnectTimerRef.current = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectDelay)
          connect()
        }, reconnectDelay)
      }
    }

    connect()

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
      if (source) {
        source.close()
      }
    }
  }, [videoId, updatePhaseRuns])
}
