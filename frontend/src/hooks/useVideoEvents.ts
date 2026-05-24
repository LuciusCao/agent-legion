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
      // Browser auto-reconnects automatically. If it permanently fails,
      // the next successful reconnect will deliver missed updates.
      // No need to spam fetchVideos() on every retry attempt.
    };

    return () => source.close();
  }, [mergeVideo, removeVideo, fetchVideos]);
}
