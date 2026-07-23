from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _mock_funasr(monkeypatch: Any) -> None:
    """Provide a minimal funasr stub so the module under test can be imported."""
    fake = ModuleType("funasr")
    setattr(fake, "AutoModel", object)  # noqa: B010
    monkeypatch.setitem(sys.modules, "funasr", fake)


@pytest.fixture
def sv() -> Any:
    import server.app.pipeline.transcribe_sensevoice as mod  # type: ignore[import-not-found]

    return mod


def test_split_by_punctuation_splits_on_punctuation(sv: Any) -> None:
    words = ["这", "是", "测", "试", "。"]
    # Duration of the current segment reaches 1.0s exactly at the punctuation.
    timestamps = [[0, 200], [200, 400], [400, 600], [600, 800], [800, 1800]]

    segments = sv.split_by_punctuation(words, timestamps)

    assert len(segments) == 1
    assert segments[0]["text"] == "这是测试。"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 1.8


def test_split_by_punctuation_splits_at_max_duration(sv: Any) -> None:
    words = ["a", "b", "c"]
    # The second character pushes the segment duration over the max.
    timestamps = [[0, 2500], [2500, 6500], [6500, 7000]]

    segments = sv.split_by_punctuation(words, timestamps, max_duration=6.0)

    assert len(segments) == 2
    assert segments[0]["text"] == "ab"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 6.5
    assert segments[1]["text"] == "c"
    assert segments[1]["start"] == 6.5
    assert segments[1]["end"] == 7.0


def test_split_by_punctuation_appends_remaining_text(sv: Any) -> None:
    words = ["x", "y"]
    timestamps = [[0, 300], [300, 600]]

    segments = sv.split_by_punctuation(words, timestamps)

    assert len(segments) == 1
    assert segments[0]["text"] == "xy"
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 0.6


def test_merge_short_segments_combines_short_neighbors(sv: Any) -> None:
    segments = [
        {"start": 0.0, "end": 0.3, "text": "a"},
        {"start": 0.35, "end": 1.5, "text": "b"},
    ]

    merged = sv.merge_short_segments(segments, min_duration=0.8)

    assert len(merged) == 1
    assert merged[0]["text"] == "ab"
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 1.5


def test_merge_short_segments_preserves_long_segments_and_large_gaps(sv: Any) -> None:
    segments = [
        {"start": 0.0, "end": 1.0, "text": "long"},
        {"start": 2.0, "end": 2.2, "text": "short"},
        {"start": 2.3, "end": 3.0, "text": "next"},
    ]

    merged = sv.merge_short_segments(segments, min_duration=0.8)

    assert len(merged) == 2
    assert merged[0]["text"] == "long"
    assert merged[1]["text"] == "shortnext"
    assert merged[1]["end"] == 3.0


def test_merge_short_segments_empty(sv: Any) -> None:
    assert sv.merge_short_segments([]) == []


def test_format_time(sv: Any) -> None:
    assert sv.format_time(0.0) == "00:00:00,000"
    assert sv.format_time(3661.123) == "01:01:01,123"
    assert sv.format_time(59.999) == "00:00:59,999"
    assert sv.format_time(60.5) == "00:01:00,500"


def test_write_srt_skips_empty_text_and_numbers_subtitles(sv: Any, tmp_path: Path) -> None:
    output_path = tmp_path / "subtitles.srt"
    segments = [
        {"start": 0.0, "end": 1.0, "text": "first"},
        {"start": 1.0, "end": 2.0, "text": "   "},
        {"start": 2.0, "end": 3.0, "text": "second"},
    ]

    sv.write_srt(segments, str(output_path))

    content = output_path.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,000\nfirst\n\n" in content
    assert "2\n00:00:02,000 --> 00:00:03,000\nsecond\n\n" in content
    assert "   " not in content


class _FakeAutoModel:
    def __init__(self, model: str, device: str, disable_update: bool) -> None:
        self.model = model
        self.device = device
        self.disable_update = disable_update


def test_transcribe_with_sensevoice_uses_local_model_dir_when_provided(
    sv: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    fake_model = {"generate_called": False}

    class FakeAutoModel(_FakeAutoModel):
        def __init__(self, model: str, device: str, disable_update: bool) -> None:
            super().__init__(model, device, disable_update)
            assert model == str(model_dir)
            assert device == "cpu"
            assert disable_update is True

        def generate(self, **kwargs: Any) -> list[dict[str, Any]]:
            fake_model["generate_called"] = True
            return []

    monkeypatch.setattr(sv, "AutoModel", FakeAutoModel)

    sv.transcribe_with_sensevoice(str(tmp_path / "audio.wav"), model_dir=str(model_dir))

    assert fake_model["generate_called"] is True


def test_transcribe_with_sensevoice_skips_items_without_timestamp(
    sv: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeAutoModel(_FakeAutoModel):
        def generate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {"text": "no timestamp", "timestamp": [], "words": []},
                {"text": "has timestamp", "timestamp": [[0, 500]], "words": ["a"]},
            ]

    monkeypatch.setattr(sv, "AutoModel", FakeAutoModel)

    segments = sv.transcribe_with_sensevoice(str(tmp_path / "audio.wav"))

    assert len(segments) == 1
    assert segments[0]["text"] == "a"


def test_transcribe_with_sensevoice_cleans_tags(sv: Any, tmp_path: Path, monkeypatch: Any) -> None:
    class FakeAutoModel(_FakeAutoModel):
        def generate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "text": "<|zh|><|NEUTRAL|>hello",
                    "timestamp": [[0, 100], [100, 200], [200, 300], [300, 400], [400, 500]],
                    "words": ["h", "e", "l", "l", "o"],
                }
            ]

    monkeypatch.setattr(sv, "AutoModel", FakeAutoModel)

    segments = sv.transcribe_with_sensevoice(str(tmp_path / "audio.wav"))

    assert segments[0]["text"] == "hello"


def test_transcribe_with_sensevoice_calibrates_abnormal_durations(
    sv: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeAutoModel(_FakeAutoModel):
        def generate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "text": "hello",
                    "timestamp": [[0, 100], [100, 3000], [3000, 3100]],
                    "words": ["h", "e", "l"],
                }
            ]

    monkeypatch.setattr(sv, "AutoModel", FakeAutoModel)

    segments = sv.transcribe_with_sensevoice(str(tmp_path / "audio.wav"))

    assert len(segments) == 1


def test_main_writes_srt_and_copies_to_default(sv: Any, tmp_path: Path, monkeypatch: Any) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_text("fake video", encoding="utf-8")
    output_dir = tmp_path / "out"

    converted: dict[str, str] = {}
    wav_path = output_dir / "title" / "title.wav"

    def fake_convert_to_wav(video: str, wav: str) -> str:
        converted["video"] = video
        converted["wav"] = wav
        Path(wav).parent.mkdir(parents=True, exist_ok=True)
        Path(wav).write_text("fake wav", encoding="utf-8")
        return wav

    def fake_transcribe(
        wav_path: str, language: str = "auto", model_dir: str | None = None
    ) -> list:
        return [{"start": 0.0, "end": 1.0, "text": "hello"}]

    monkeypatch.setattr(sv, "convert_to_wav", fake_convert_to_wav)
    monkeypatch.setattr(sv, "transcribe_with_sensevoice", fake_transcribe)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "transcribe_sensevoice.py",
            "--input",
            str(video_path),
            "--title",
            "title",
            "--output-dir",
            str(output_dir),
        ],
    )

    sv.main()

    assert converted["video"] == str(video_path)
    assert converted["wav"] == str(wav_path)
    assert (output_dir / "title" / "subtitles.srt.sensevoice").exists()
    assert (output_dir / "title" / "subtitles.srt").exists()
