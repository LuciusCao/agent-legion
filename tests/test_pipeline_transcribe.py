import pytest

from server.app.pipeline.transcribe import (
    TranscriptionProvider,
    run_transcription_with_providers,
    validate_srt,
)
from tests.conftest import BadProvider, GoodProvider


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
    text = "\n\n".join(
        f"{i}\n00:00:0{i},000 --> 00:00:0{i+1},000\nsame text"
        for i in range(5)
    )
    result = validate_srt(text)
    assert result.ok is False
    assert "overly repetitive" in result.summary


def test_validate_srt_valid():
    text = (
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n"
    )
    result = validate_srt(text, duration=10)
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
