import shutil
import subprocess
import sys
from pathlib import Path

from server.app.pipeline.transcribe import TranscriptionProvider


class WhisperCppProvider(TranscriptionProvider):
    name = "whisper"

    def __init__(self, binary: str, model: str, vad_model: str | None = None, timeout: int = 900):
        binary_path = Path(binary).expanduser()
        if not binary_path.exists():
            found = shutil.which(binary)
            if found:
                binary_path = Path(found)
        self.binary = binary_path
        self.model = Path(model).expanduser()
        self.vad_model = Path(vad_model).expanduser() if vad_model else None
        self.timeout = timeout

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        if not self.binary.exists():
            raise FileNotFoundError(
                f"whisper binary not found: {self.binary} (set env AGENT_LEGION_ASR_WHISPER_BINARY)"
            )
        if not self.model.exists():
            raise FileNotFoundError(
                f"whisper model not found: {self.model} (set env AGENT_LEGION_ASR_WHISPER_MODEL)"
            )
        wav_path = output_path.with_suffix(".wav")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout,
            )
            prompt = f"简体中文 {title}" if title else "简体中文"
            out_stem = output_path.with_suffix("")
            cmd = [
                str(self.binary),
                "-m",
                str(self.model),
                "-f",
                str(wav_path),
                "--language",
                "zh",
                "--prompt",
                prompt,
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
                cmd.extend(
                    [
                        "--vad",
                        "--vad-model",
                        str(self.vad_model),
                        "--vad-max-speech-duration-s",
                        "8",
                    ]
                )
            subprocess.run(cmd, check=True, timeout=self.timeout)
            raw_srt = out_stem.with_suffix(".srt")
            if raw_srt != output_path and raw_srt.exists():
                shutil.move(raw_srt, output_path)
        finally:
            wav_path.unlink(missing_ok=True)


class SenseVoiceProvider(TranscriptionProvider):
    name = "sensevoice"

    def __init__(self, script: str, model_dir: str | None = None, timeout: int = 900):
        self.script = Path(script).expanduser()
        self.model_dir = Path(model_dir).expanduser() if model_dir else None
        self.timeout = timeout

    def transcribe(self, video_path: Path, output_path: Path, title: str) -> None:
        if not self.script.exists():
            raise FileNotFoundError(
                f"SenseVoice script not found: {self.script} "
                "(set env AGENT_LEGION_ASR_SENSEVOICE_SCRIPT)"
            )
        video_id = video_path.stem
        script_output_dir = (
            output_path.parent.parent if output_path.parent.name == video_id else output_path.parent
        )
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
        if self.model_dir and self.model_dir.exists():
            cmd.extend(["--model-dir", str(self.model_dir)])
        result = subprocess.run(
            cmd,
            timeout=self.timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr.strip()[-500:]
            raise RuntimeError(
                f"SenseVoice failed with exit code {result.returncode}: {stderr_tail}"
            )
        produced = script_output_dir / video_id / "subtitles.srt"
        if not produced.exists():
            produced = output_path.parent / "subtitles.srt"
        if not produced.exists():
            raise FileNotFoundError(f"SenseVoice output not found: {produced}")
        if produced != output_path:
            shutil.copy2(produced, output_path)
