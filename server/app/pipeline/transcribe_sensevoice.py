#!/usr/bin/env python3
"""
使用 SenseVoice 进行语音识别并生成 SRT 字幕。

用法：
    python3 transcribe_sensevoice.py --input <local_video_path> --title <video_id> --output-dir <dir>
"""

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from funasr import AutoModel
except ImportError:
    logger.error("funasr not installed. Install with: pip install funasr")
    sys.exit(1)

SENSEVOICE_MODEL = "iic/SenseVoiceSmall"


def convert_to_wav(video_path: str, wav_path: str) -> str:
    """Convert video to 16kHz mono WAV."""
    logger.info("Convert %s -> %s", video_path, wav_path)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            wav_path,
        ],
        check=True,
        capture_output=True,
    )
    return wav_path


def format_time(seconds: float) -> str:
    """Format seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(segments: list, output_path: str):
    """Write segments to SRT file."""
    valid = [s for s in segments if s["text"].strip()]
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(valid, 1):
            start = format_time(seg["start"])
            end = format_time(seg["end"])
            f.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")
    logger.info("SRT saved: %s (%s subtitles)", output_path, len(valid))


def split_by_punctuation(words: list, timestamp: list, max_duration: float = 6.0) -> list:
    """Split text into subtitle segments by punctuation and max duration.

    Uses character-level timestamps from SenseVoice to ensure accurate timing.
    """
    segments = []
    seg_text = ""
    seg_start: float | None = None
    seg_end: float | None = None

    for _i, (char, (char_start, char_end)) in enumerate(zip(words, timestamp, strict=True)):
        if seg_start is None:
            seg_start = char_start

        seg_text += char
        seg_end = char_end

        assert seg_start is not None
        assert seg_end is not None
        duration = (seg_end - seg_start) / 1000.0
        is_punctuation = char in "。，！？；,.!?;"

        # Split at punctuation when segment is long enough, or when max duration is reached
        if (is_punctuation and duration >= 1.0) or duration >= max_duration:
            segments.append(
                {"start": seg_start / 1000.0, "end": seg_end / 1000.0, "text": seg_text.strip()}
            )
            seg_text = ""
            seg_start = None

    # Append remaining text
    if seg_text.strip() and seg_start is not None and seg_end is not None:
        segments.append(
            {"start": seg_start / 1000.0, "end": seg_end / 1000.0, "text": seg_text.strip()}
        )

    return segments


def merge_short_segments(segments: list, min_duration: float = 0.8) -> list:
    """Merge segments that are too short.

    Only merges a short segment into the next one if they are adjacent (small gap).
    Preserves accurate timestamps.
    """
    if not segments:
        return []

    merged = []
    current = segments[0].copy()

    for seg in segments[1:]:
        current_duration = current["end"] - current["start"]
        gap = seg["start"] - current["end"]

        # Merge only if current is very short AND close to next segment
        if current_duration < min_duration and gap < 0.5:
            current["end"] = seg["end"]
            current["text"] += seg["text"]
        else:
            merged.append(current)
            current = seg.copy()

    merged.append(current)
    return merged


def transcribe_with_sensevoice(
    wav_path: str, language: str = "auto", model_dir: str | None = None
) -> list:
    """Transcribe audio using SenseVoice model."""
    model_path = model_dir if model_dir and Path(model_dir).exists() else SENSEVOICE_MODEL
    logger.info("Loading SenseVoice model: %s", model_path)
    model = AutoModel(
        model=model_path,
        device="cpu",
        disable_update=True,
    )

    logger.info("Transcribing: %s", wav_path)
    result = model.generate(
        input=wav_path,
        language=language,
        use_itn=True,
        output_timestamp=True,
        return_raw_text=True,
    )

    segments = []
    for item in result:
        if not isinstance(item, dict):
            continue

        text = item.get("text", "")
        timestamp = item.get("timestamp", [])
        words = item.get("words", [])

        # Clean up emotion/language tags
        text = re.sub(r"<\|[^|]+\|>", "", text).strip()

        if not timestamp or not words:
            logger.warning("No timestamp in result")
            continue

        # Calibrate timestamps: SenseVoice sometimes absorbs silence/noise into
        # a character's duration (especially after long pauses). Detect abnormal
        # durations using IQR-based outlier detection, with a hard floor of 2s.
        char_durations = [timestamp[i][1] - timestamp[i][0] for i in range(len(timestamp))]
        if char_durations:
            d = sorted(char_durations)
            q3 = d[len(d) * 3 // 4]
            iqr = q3 - d[len(d) // 4]
            threshold = max(q3 + 5 * iqr, 2000)  # At least 2 seconds
            median_duration = d[len(d) // 2]
            for i in range(len(timestamp)):
                dur = char_durations[i]
                if dur > threshold:
                    new_start = timestamp[i][1] - median_duration
                    # Prevent overlap with previous character
                    if i > 0 and new_start < timestamp[i - 1][1]:
                        new_start = timestamp[i - 1][1]
                    if new_start >= 0 and new_start < timestamp[i][1]:
                        logger.info(
                            "Calibrate char #%s duration %sms abnormal, adjusting start %s -> %sms",
                            i,
                            dur,
                            timestamp[i][0],
                            new_start,
                        )
                        timestamp[i][0] = new_start

        # Build segments from character-level timestamps
        segments = split_by_punctuation(words, timestamp)

    logger.info("Raw segments: %s", len(segments))

    # Merge very short segments
    segments = merge_short_segments(segments)
    logger.info("After merge: %s", len(segments))

    return segments


def main():
    parser = argparse.ArgumentParser(description="Transcribe video with SenseVoice")
    parser.add_argument("--input", required=True, help="Local video file path")
    parser.add_argument("--title", required=True, help="Video ID/title for output naming")
    parser.add_argument(
        "--output-dir", default="videos/pipeline-output/_shared", help="Output directory"
    )
    parser.add_argument(
        "--language", default="auto", choices=["auto", "zh", "en", "ja", "ko", "yue"]
    )
    parser.add_argument("--model-dir", default=None, help="Local SenseVoice model directory")

    args = parser.parse_args()

    output_dir = Path(args.output_dir) / args.title
    output_dir.mkdir(parents=True, exist_ok=True)

    video_path = Path(args.input)
    wav_path = output_dir / f"{args.title}.wav"
    srt_path = output_dir / "subtitles.srt.sensevoice"
    srt_default = output_dir / "subtitles.srt"

    # Convert to WAV
    convert_to_wav(str(video_path), str(wav_path))

    # Transcribe
    segments = transcribe_with_sensevoice(
        str(wav_path), language=args.language, model_dir=args.model_dir
    )

    # Write SRT
    write_srt(segments, str(srt_path))
    import shutil

    shutil.copy2(str(srt_path), str(srt_default))

    logger.info("Done. Output: %s", srt_path)
    logger.info("Also saved: %s", srt_default)
    logger.info("WAV kept: %s", wav_path)
    logger.info("Segments: %s", len(segments))


if __name__ == "__main__":
    main()
