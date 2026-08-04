import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from server.app.pipeline.common import parse_srt


class TranscriptionProvider:
    name = "provider"

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        raise NotImplementedError


@dataclass
class ValidationResult:
    ok: bool
    entry_count: int
    summary: str


@dataclass
class TranscriptionResult:
    provider: str
    srt_entry_count: int
    validation_summary: str
    fallback_reason: str = ""


def validate_srt(text: str, duration: float = 0) -> ValidationResult:
    subtitles = parse_srt(text)
    if not text.strip():
        return ValidationResult(False, 0, "empty srt")
    if not subtitles:
        return ValidationResult(False, 0, "no parseable srt entries")
    non_empty = [s for s in subtitles if s["text"].strip()]
    if len(non_empty) < len(subtitles) * 0.5:
        return ValidationResult(False, len(subtitles), "too many empty subtitle entries")
    if duration and subtitles[-1]["end"] < min(duration * 0.25, max(duration - 10, 1)):
        return ValidationResult(False, len(subtitles), "subtitle coverage too low")
    if len(subtitles) >= 2:
        max_gap = max(
            s2["start"] - s1["end"] for s1, s2 in zip(subtitles, subtitles[1:], strict=False)
        )
        if max_gap > 20:
            return ValidationResult(
                False, len(subtitles), f"subtitle gap too large: {max_gap:.1f}s"
            )
    max_entry_duration = max(s["end"] - s["start"] for s in subtitles)
    if max_entry_duration > 15:
        return ValidationResult(
            False, len(subtitles), f"subtitle entry too long: {max_entry_duration:.1f}s"
        )
    texts = [s["text"].strip() for s in subtitles]
    if len(set(texts)) <= 1 and len(texts) >= 3:
        return ValidationResult(False, len(subtitles), "subtitle text is overly repetitive")
    return ValidationResult(True, len(subtitles), f"valid srt with {len(subtitles)} entries")


def run_transcription_with_providers(
    video_path: Path,
    output_dir: Path,
    title: str,
    duration: float,
    mode: str,
    providers: list[TranscriptionProvider],
) -> TranscriptionResult:
    output_path = output_dir / "subtitles.srt"
    wanted = providers if mode == "auto" else [p for p in providers if p.name == mode]
    if not wanted:
        raise ValueError(f"No transcription provider for mode: {mode}")

    failures: list[str] = []
    for idx, provider in enumerate(wanted):
        tmp_output = output_dir / f"subtitles.{provider.name}.srt"
        tmp_output.unlink(missing_ok=True)
        try:
            provider.transcribe(video_path, tmp_output, title)
            validation = validate_srt(tmp_output.read_text(encoding="utf-8"), duration)
        except Exception as exc:
            validation = ValidationResult(False, 0, f"{provider.name} error: {exc}")

        if validation.ok:
            shutil.move(tmp_output, output_path)
            fallback = "; ".join(failures)
            summary = validation.summary
            if idx > 0:
                summary = f"fallback succeeded after {fallback}; {summary}"
            meta = {
                "provider": provider.name,
                "entry_count": validation.entry_count,
                "validation_summary": summary,
                "fallback_reason": fallback,
            }
            (output_dir / "transcription.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return TranscriptionResult(provider.name, validation.entry_count, summary, fallback)

        failures.append(f"{provider.name}: {validation.summary}")
        tmp_output.unlink(missing_ok=True)

    raise RuntimeError("All transcription providers failed: " + "; ".join(failures))
