from subprocess import TimeoutExpired
from unittest.mock import patch

import pytest

from server.app.pipeline.transcribe import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
    run_transcription_with_providers,
    validate_srt,
)
from tests.helpers import BadProvider, GoodProvider


def test_transcribe_auto_falls_back_to_sensevoice(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    result = run_transcription_with_providers(
        video_path=video_path,
        output_dir=tmp_path,
        title="A",
        duration=20,
        mode="auto",
        providers=[BadProvider(), GoodProvider()],
    )

    assert result.provider == "sensevoice"
    assert result.srt_entry_count == 2
    assert "fallback" in result.validation_summary
    assert validate_srt((tmp_path / "subtitles.srt").read_text(encoding="utf-8"), 20).ok


def test_transcribe_whisper_mode_uses_only_whisper_provider(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    class WhisperProvider(TranscriptionProvider):
        name = "whisper"

        def transcribe(self, video_path, output_path, title):
            output_path.write_text(
                "1\n00:00:00,000 --> 00:00:05,000\nWhisper result\n", encoding="utf-8"
            )

    result = run_transcription_with_providers(
        video_path=video_path,
        output_dir=tmp_path,
        title="A",
        duration=10,
        mode="whisper",
        providers=[WhisperProvider(), GoodProvider()],
    )

    assert result.provider == "whisper"
    assert result.srt_entry_count == 1


def test_transcribe_sensevoice_mode_uses_only_sensevoice_provider(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    result = run_transcription_with_providers(
        video_path=video_path,
        output_dir=tmp_path,
        title="A",
        duration=10,
        mode="sensevoice",
        providers=[BadProvider(), GoodProvider()],
    )

    assert result.provider == "sensevoice"
    assert result.srt_entry_count == 2


def test_transcribe_mode_with_no_matching_provider_raises(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    with pytest.raises(ValueError, match="No transcription provider for mode"):
        run_transcription_with_providers(
            video_path=video_path,
            output_dir=tmp_path,
            title="A",
            duration=10,
            mode="whisper",
            providers=[GoodProvider()],  # only sensevoice
        )


def test_transcribe_all_providers_fail_raises_runtime_error(tmp_path):
    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="All transcription providers failed"):
        run_transcription_with_providers(
            video_path=video_path,
            output_dir=tmp_path,
            title="A",
            duration=10,
            mode="auto",
            providers=[BadProvider()],
        )


def test_validate_srt_empty_file():
    result = validate_srt("")
    assert result.ok is False
    assert result.summary == "empty srt"


def test_validate_srt_unparseable():
    result = validate_srt("not srt at all")
    assert result.ok is False
    assert result.summary == "no parseable srt entries"


def test_validate_srt_coverage_too_low():
    text = "1\n00:00:00,000 --> 00:00:01,000\nHello\n"
    result = validate_srt(text, duration=100)
    assert result.ok is False
    assert "subtitle coverage too low" in result.summary


def test_validate_srt_overly_repetitive():
    text = "\n\n".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i + 1},000\nsame text" for i in range(5))
    result = validate_srt(text)
    assert result.ok is False
    assert "overly repetitive" in result.summary


def test_validate_srt_valid():
    text = "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nWorld\n"
    result = validate_srt(text, duration=10)
    assert result.ok is True
    assert result.entry_count == 2


def test_validate_srt_large_gap_fails():
    text = "1\n00:00:00,000 --> 00:00:04,000\nHello\n\n2\n00:00:30,000 --> 00:00:35,000\nWorld\n"
    result = validate_srt(text, duration=40)
    assert result.ok is False
    assert "gap too large" in result.summary
    assert "26.0s" in result.summary


def test_validate_srt_small_gap_passes():
    text = "1\n00:00:00,000 --> 00:00:04,000\nHello\n\n2\n00:00:14,000 --> 00:00:18,000\nWorld\n"
    result = validate_srt(text, duration=20)
    assert result.ok is True
    assert result.entry_count == 2


def test_validate_srt_entry_too_long_fails():
    text = "1\n00:00:00,000 --> 00:00:20,000\nHello world this is a very long segment\n"
    result = validate_srt(text, duration=30)
    assert result.ok is False
    assert "entry too long" in result.summary
    assert "20.0s" in result.summary


def test_validate_srt_entry_within_limit_passes():
    text = (
        "1\n00:00:00,000 --> 00:00:14,000\nHello world\n\n2\n00:00:14,000 --> 00:00:18,000\nWorld\n"
    )
    result = validate_srt(text, duration=20)
    assert result.ok is True
    assert result.entry_count == 2


def test_transcribe_provider_exception_becomes_validation_failure(tmp_path):
    class ExplodingProvider(TranscriptionProvider):
        name = "boom"

        def transcribe(self, video_path, output_path, title):
            raise RuntimeError("exploded")

    video_path = tmp_path / "a.mp4"
    video_path.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="All transcription providers failed"):
        run_transcription_with_providers(
            video_path=video_path,
            output_dir=tmp_path,
            title="A",
            duration=10,
            mode="auto",
            providers=[ExplodingProvider()],
        )

    # transcription.json should NOT be written when all fail
    assert not (tmp_path / "transcription.json").exists()


def _make_fake_whisper_provider(tmp_path, vad_model=None):
    binary = tmp_path / "whisper-cli"
    binary.write_bytes(b"fake")
    model = tmp_path / "model.bin"
    model.write_bytes(b"fake")
    return WhisperCppProvider(binary=str(binary), model=str(model), vad_model=vad_model)


def test_whisper_provider_without_vad_omits_vad_flags(tmp_path):
    provider = _make_fake_whisper_provider(tmp_path)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"

    with patch("server.app.pipeline.transcribe.subprocess.run") as mock_run:
        provider.transcribe(video_path, output_path, "Test")

    # Second call is whisper-cli
    whisper_call = mock_run.call_args_list[1]
    cmd = whisper_call.args[0]
    assert "--vad" not in cmd
    assert "--vad-model" not in cmd


def test_whisper_provider_with_vad_includes_vad_flags(tmp_path):
    vad_model = tmp_path / "vad.bin"
    vad_model.write_bytes(b"fake")
    provider = _make_fake_whisper_provider(tmp_path, vad_model=str(vad_model))
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"

    with patch("server.app.pipeline.transcribe.subprocess.run") as mock_run:
        provider.transcribe(video_path, output_path, "Test")

    whisper_call = mock_run.call_args_list[1]
    cmd = whisper_call.args[0]
    assert "--vad" in cmd
    assert "--vad-model" in cmd
    assert str(vad_model) in cmd
    assert "--vad-max-speech-duration-s" in cmd
    assert "8" in cmd


def test_whisper_provider_with_missing_vad_model_raises(tmp_path):
    provider = _make_fake_whisper_provider(tmp_path, vad_model=str(tmp_path / "missing.bin"))
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"

    with (
        patch("server.app.pipeline.transcribe.subprocess.run"),
        pytest.raises(FileNotFoundError, match="VAD model not found"),
    ):
        provider.transcribe(video_path, output_path, "Test")


def test_whisper_provider_timeout_cleans_temp_wav(tmp_path):
    provider = _make_fake_whisper_provider(tmp_path)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"
    wav_path = output_path.with_suffix(".wav")

    def _fake_run(cmd, **kwargs):
        # Simulate ffmpeg creating the wav file
        if cmd[0] == "ffmpeg":
            wav_path.write_bytes(b"fake wav")
            return
        # Simulate whisper timing out
        raise TimeoutExpired(cmd, timeout=900)

    with (
        patch("server.app.pipeline.transcribe.subprocess.run", side_effect=_fake_run),
        pytest.raises(TimeoutExpired),
    ):
        provider.transcribe(video_path, output_path, "Test")

    assert not wav_path.exists(), "temporary wav should be cleaned on timeout"


def _make_fake_sensevoice_provider(tmp_path, model_dir=None):
    script = tmp_path / "transcribe_sensevoice.py"
    script.write_bytes(b"fake script")
    return SenseVoiceProvider(script=str(script), model_dir=str(model_dir) if model_dir else None)


def test_sensevoice_provider_includes_model_dir_when_present(tmp_path):
    model_dir = tmp_path / "SenseVoiceSmall"
    model_dir.mkdir()
    provider = _make_fake_sensevoice_provider(tmp_path, model_dir=str(model_dir))
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"
    expected_output = tmp_path / "video" / "subtitles.srt"

    def _fake_run(cmd, **kwargs):
        expected_output.parent.mkdir(parents=True, exist_ok=True)
        expected_output.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    with patch("server.app.pipeline.transcribe.subprocess.run", side_effect=_fake_run) as mock_run:
        provider.transcribe(video_path, output_path, "Test")

    cmd = mock_run.call_args[0][0]
    assert "--model-dir" in cmd
    assert str(model_dir) in cmd


def test_sensevoice_provider_omits_model_dir_when_missing(tmp_path):
    provider = _make_fake_sensevoice_provider(tmp_path, model_dir=str(tmp_path / "missing"))
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"
    expected_output = tmp_path / "video" / "subtitles.srt"

    def _fake_run(cmd, **kwargs):
        expected_output.parent.mkdir(parents=True, exist_ok=True)
        expected_output.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    with patch("server.app.pipeline.transcribe.subprocess.run", side_effect=_fake_run) as mock_run:
        provider.transcribe(video_path, output_path, "Test")

    cmd = mock_run.call_args[0][0]
    assert "--model-dir" not in cmd


def test_sensevoice_provider_omits_model_dir_when_none(tmp_path):
    provider = _make_fake_sensevoice_provider(tmp_path, model_dir=None)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    output_path = tmp_path / "subtitles.srt"
    expected_output = tmp_path / "video" / "subtitles.srt"

    def _fake_run(cmd, **kwargs):
        expected_output.parent.mkdir(parents=True, exist_ok=True)
        expected_output.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    with patch("server.app.pipeline.transcribe.subprocess.run", side_effect=_fake_run) as mock_run:
        provider.transcribe(video_path, output_path, "Test")

    cmd = mock_run.call_args[0][0]
    assert "--model-dir" not in cmd
