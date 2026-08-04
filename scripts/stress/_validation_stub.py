"""Startup-validation stubs for the stress backend (Phase 4C).

The stress backend runs the module-level app (start_worker=True), so its
lifespan executes ``validate_settings``: ASR auto mode demands a usable
whisper/sensevoice install and an enabled CMS resource demands credentials.
Stress never transcribes audio and never calls the real CMS, so the runner
satisfies the validators with local stubs instead of installing whisper or
shipping credentials:

- a no-op ``whisper-cli`` executable and an empty model file under
  ``data/stress/asr-stub`` (paths injected via the AGENT_LEGION_ASR_* env
  overrides, keeping $HOME untouched);
- a dummy CMS token via env, the sanctioned env-only channel (CONFIG G2).
"""

from __future__ import annotations

from pathlib import Path

STUB_DIR_NAME = "asr-stub"


def ensure_asr_validation_stub(stress_data_dir: Path) -> dict[str, str]:
    """Create the stub binary/model once and return the env overrides."""
    stub_dir = stress_data_dir / STUB_DIR_NAME
    stub_dir.mkdir(parents=True, exist_ok=True)
    binary = stub_dir / "whisper-cli"
    if not binary.exists():
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
    model = stub_dir / "ggml-stub.bin"
    model.touch(exist_ok=True)
    return {
        "AGENT_LEGION_ASR_WHISPER_BINARY": str(binary),
        "AGENT_LEGION_ASR_WHISPER_MODEL": str(model),
        # Presence-only startup validation; stress never calls the real CMS.
        "AGENT_LEGION_CMS_TOKEN": "stress-validation-dummy-token",
    }
