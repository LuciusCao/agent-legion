import { useEffect } from "react";
import { useVideoStore } from "../stores/videoStore";

export function useVideoEvents() {
  const { mergeVideo, removeVideo, fetchVideos } = useVideoStore();

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource("/api/videos/events");

    source.onmessage = (event) => {
      if (!event.data || event.data.startsWith(":heartbeat")) return;
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "video_updated" && payload.video) {
          mergeVideo(payload.video);
        } else if (payload.type === "video_deleted" && payload.video_id) {
          removeVideo(payload.video_id);
        }
      } catch {
        // ignore invalid payloads
      }
    };

    source.onerror = () => {
      // Auto-reconnect is handled by the browser; just ensure the
      // current list is fresh when the connection drops.
      fetchVideos();
    };

    return () => source.close();
  }, [mergeVideo, removeVideo, fetchVideos]);
}
