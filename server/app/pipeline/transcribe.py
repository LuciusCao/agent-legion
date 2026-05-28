import json
import shutil
import subprocess
import sys
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
            return ValidationResult(False, len(subtitles), f"subtitle gap too large: {max_gap:.1f}s")
    max_entry_duration = max(s["end"] - s["start"] for s in subtitles)
    if max_entry_duration > 15:
        return ValidationResult(
            False, len(subtitles), f"subtitle entry too long: {max_entry_duration:.1f}s"
        )
    texts = [s["text"].strip() for s in subtitles]
    if len(set(texts)) <= 1 and len(texts) >= 3:
        return ValidationResult(False, len(subtitles), "subtitle text is overly repetitive")
    return ValidationResult(True, len(subtitles), f"valid srt with {len(subtitles)} entries")


class WhisperCppProvider(TranscriptionProvider):
    name = "whisper"

    def __init__(self, binary: str, model: str, vad_model: str | None = None):
        self.binary = Path(binary).expanduser()
        self.model = Path(model).expanduser()
        self.vad_model = Path(vad_model).expanduser() if vad_model else None

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        if not self.binary.exists():
            raise FileNotFoundError(f"whisper binary not found: {self.binary}")
        if not self.model.exists():
            raise FileNotFoundError(f"whisper model not found: {self.model}")
        wav_path = output_path.with_suffix(".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        out_stem = output_path.with_suffix("")
        cmd = [
            str(self.binary),
            "-m",
            str(self.model),
            "-f",
            str(wav_path),
            "--language",
            "zh",
            "--output-srt",
            "-of",
            str(out_stem),
            "--max-len",
            "8",  # Limit segment length to ~8 chars
            "--split-on-word",  # Split at word boundaries
        ]
        if self.vad_model:
            if not self.vad_model.exists():
                raise FileNotFoundError(f"VAD model not found: {self.vad_model}")
            cmd.extend([
                "--vad",
                "--vad-model",
                str(self.vad_model),
                "--vad-max-speech-duration-s",
                "8",
            ])
        subprocess.run(cmd, check=True)
        raw_srt = out_stem.with_suffix(".srt")
        if raw_srt != output_path and raw_srt.exists():
            shutil.move(raw_srt, output_path)
        wav_path.unlink(missing_ok=True)


class SenseVoiceProvider(TranscriptionProvider):
    name = "sensevoice"

    def __init__(self, script: str, model_dir: str | None = None):
        self.script = Path(script).expanduser()
        self.model_dir = Path(model_dir).expanduser() if model_dir else None

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        if not self.script.exists():
            raise FileNotFoundError(f"SenseVoice script not found: {self.script}")
        video_id = video_path.stem
        script_output_dir = output_path.parent.parent if output_path.parent.name == video_id else output_path.parent
        cmd = [
            sys.executable,
            str(self.script),
            "--input",
            str(video_path),
            "--title",
            video_id,
            "--output-dir",
            str(script_output_dir),
        ]
        subprocess.run(cmd, check=True)
        produced = script_output_dir / video_id / "subtitles.srt"
        if not produced.exists():
            produced = output_path.parent / "subtitles.srt"
        if not produced.exists():
            raise FileNotFoundError(f"SenseVoice output not found: {produced}")
        if produced != output_path:
            shutil.copy2(produced, output_path)


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
