import type { TranscriptionRun } from "../types";
import { formatDuration } from "../lib/formatters";

interface TranscriptionDetailsProps {
  primary: TranscriptionRun | undefined;
  fallback: TranscriptionRun | undefined;
  totalCount: number;
}

export function TranscriptionDetails({ primary, fallback, totalCount }: TranscriptionDetailsProps) {
  return (
    <div className="timeline-detail-content transcription">
      <div className="trans-row">
        <span className="trans-key">Provider</span>
        <span className="trans-value">{primary?.provider || "—"}</span>
      </div>
      {primary && primary.srt_entry_count > 0 && (
        <div className="trans-row">
          <span className="trans-key">字幕条目</span>
          <span className="trans-value">{primary.srt_entry_count}</span>
        </div>
      )}
      {primary?.validation_summary && (
        <div className="trans-row">
          <span className="trans-key">验证结果</span>
          <span className="trans-value">{primary.validation_summary}</span>
        </div>
      )}
      {fallback?.fallback_reason && (
        <div className="trans-row">
          <span className="trans-key">Fallback</span>
          <span className="trans-value">{fallback.fallback_reason}</span>
        </div>
      )}
      {totalCount > 1 && (
        <div className="trans-row">
          <span className="trans-key">尝试次数</span>
          <span className="trans-value">{totalCount}</span>
        </div>
      )}
    </div>
  );
}
