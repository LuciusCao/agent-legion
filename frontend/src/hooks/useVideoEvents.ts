import { useEffect, useRef } from 'react'
import { useVideoStore } from '../stores/videoStore'
import { triggerDownload } from '../lib/download'

export function useVideoEvents() {
  const { mergeVideo, removeVideo, setSseConnected } = useVideoStore()
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (typeof EventSource === 'undefined') return

    let source: EventSource | null = null
    let reconnectDelay = 1000
    const maxReconnectDelay = 30000

    const connect = () => {
      if (source) return
      source = new EventSource('/api/videos/events')

      source.onopen = () => {
        reconnectDelay = 1000
        setSseConnected(true)
      }

      source.onmessage = (event) => {
        if (!event.data || event.data.startsWith(':heartbeat')) return
        try {
          const payload = JSON.parse(event.data)
          if (payload.type === 'video_updated' && payload.video) {
            mergeVideo(payload.video)
          } else if (payload.type === 'video_deleted' && payload.video_id) {
            removeVideo(payload.video_id)
          } else if (payload.type === 'package_ready' && payload.download_url) {
            triggerDownload(payload.download_url)
          }
        } catch {
          // ignore invalid payloads
        }
      }

      source.onerror = () => {
        setSseConnected(false)
        if (source) {
          source.close()
          source = null
        }
        // Exponential backoff reconnect
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
  }, [mergeVideo, removeVideo, setSseConnected])
}
