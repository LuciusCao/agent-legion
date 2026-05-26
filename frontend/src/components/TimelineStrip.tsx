import type { Chapter, InteractionNode } from "../types";
import styles from "./TimelineStrip.module.css";

interface TimelineStripProps {
  chapters: Chapter[];
  interactions: InteractionNode[];
  duration: number;
  currentTime: number;
  onSeek: (time: number) => void;
  onReplayInteraction?: (index: number) => void;
}

export function TimelineStrip({
  chapters,
  interactions,
  duration,
  currentTime,
  onSeek,
  onReplayInteraction,
}: TimelineStripProps) {
  const safeDuration = Math.max(duration, 1);

  const handleTrackClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const clamped = Math.max(0, Math.min(1, ratio));
    onSeek(clamped * safeDuration);
  };

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const chipItems = [
    ...chapters.map((c, i) => ({
      type: "chapter" as const,
      index: i,
      time: c.start,
      label: c.title,
      end: chapters[i + 1]?.start ?? safeDuration,
    })),
    ...interactions.map((n, i) => ({
      type: "interaction" as const,
      index: i,
      time: Number(n.trigger_time ?? 0),
      label: n.instruction || "交互节点",
      end: null,
    })),
  ].sort((a, b) => a.time - b.time);

  const seekInteraction = (time: number, index: number) => {
    onSeek(time);
    onReplayInteraction?.(index);
  };

  return (
    <div className={styles.timelineContainer}>
      <div className={styles.chipRow}>
        {chipItems.map((item) => {
          if (item.type === "chapter") {
            const isActive = currentTime >= item.time && currentTime < item.end;
            return (
              <md-suggestion-chip
                key={`ch-${item.index}`}
                class={isActive ? styles.activeChip : ""}
                label={`${formatTime(item.time)} ${item.label}`}
                onClick={() => onSeek(item.time)}
              />
            );
          }

          const isActive = currentTime >= item.time && currentTime < item.time + 1.5;
          return (
            <md-suggestion-chip
              key={`in-${item.index}`}
              class={isActive ? styles.activeInteractionChip : styles.interactionChip}
              label={`${formatTime(item.time)} ${item.label}`}
              onClick={() => seekInteraction(item.time, item.index)}
            />
          );
        })}
      </div>

      <div className={styles.trackWrapper}>
        <div className={styles.track} onClick={handleTrackClick}>
          {chapters.map((chapter, i) => {
            const start = chapter.start;
            const end = chapters[i + 1]?.start ?? safeDuration;
            const left = (start / safeDuration) * 100;
            const width = ((end - start) / safeDuration) * 100;
            const isActive = currentTime >= start && currentTime < end;
            return (
              <div
                key={`seg-${i}`}
                className={`${styles.chapterSegment} ${isActive ? styles.activeSegment : ""}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                onClick={(e) => {
                  e.stopPropagation();
                  onSeek(start);
                }}
                title={`${chapter.title} (${formatTime(start)} - ${formatTime(end)})`}
              />
            );
          })}

          {interactions.map((node, i) => {
            const time = Number(node.trigger_time ?? 0);
            const left = (time / safeDuration) * 100;
            return (
              <div
                key={`mark-${i}`}
                className={styles.nodeMarker}
                style={{ left: `${left}%` }}
                onClick={(e) => {
                  e.stopPropagation();
                  seekInteraction(time, i);
                }}
                title={`${node.instruction || "交互节点"} @ ${formatTime(time)}`}
              />
            );
          })}

          <div
            className={styles.currentTimeIndicator}
            style={{ left: `${(Math.min(currentTime, safeDuration) / safeDuration) * 100}%` }}
          />
        </div>

        <div className={styles.timeLabels}>
          <span>{formatTime(0)}</span>
          <span>{formatTime(safeDuration)}</span>
        </div>
      </div>
    </div>
  );
}
