import { useMemo } from "react";
import { useDetailStore } from "../stores/detailStore";

export function SubtitlePanel({ currentTime, onSeek }: { currentTime: number; onSeek: (time: number) => void }) {
  const { artifacts } = useDetailStore();
  const subtitles = artifacts.subtitles;

  const activeIndex = useMemo(() => {
    return subtitles.findIndex((s) => currentTime >= s.start && currentTime < s.end);
  }, [subtitles, currentTime]);

  return (
    <md-list className="tab-panel">
      {subtitles.map((sub, i) => (
        <md-list-item
          key={i}
          type="button"
          className={i === activeIndex ? "active" : ""}
          onClick={() => onSeek(sub.start)}
        >
          <div slot="headline" style={{ fontVariantNumeric: "tabular-nums", minWidth: "100px" }}>
            {formatTime(sub.start)} → {formatTime(sub.end)}
          </div>
          <div slot="supporting-text">{sub.text}</div>
        </md-list-item>
      ))}
    </md-list>
  );
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
