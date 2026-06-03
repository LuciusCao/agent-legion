import { useEffect, useRef } from 'react'
import { useVideoStore } from '../stores/videoStore'
import { triggerDownload } from '../lib/download'
import { fetchPackages } from '../api'

const LAST_DOWNLOADED_KEY = 'video-hive:last-downloaded-package-id'

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

    // On page load, check for packages created while the page was away
    // (e.g. user refreshed during packaging). If the latest package has
    // not been downloaded yet, trigger the download automatically.
    const checkPendingPackages = async () => {
      try {
        const data = await fetchPackages()
        const packages = data.packages || []
        if (packages.length === 0) return
        const latest = packages[0]
        const lastDownloaded = localStorage.getItem(LAST_DOWNLOADED_KEY)
        if (String(latest.id) === lastDownloaded) return
        const filename = latest.path.split('/').pop() || ''
        if (!filename) return
        localStorage.setItem(LAST_DOWNLOADED_KEY, String(latest.id))
        triggerDownload(`/api/packages/${filename}`)
      } catch {
        // ignore: no packages or network error
      }
    }

    checkPendingPackages()

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
