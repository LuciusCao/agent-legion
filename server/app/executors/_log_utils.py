from __future__ import annotations

from pathlib import Path


def copy_pi_logs(run_dir: Path, log_path: Path) -> None:
    """Copy the Pi runner's event and stderr logs to the context log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        (run_dir / name).read_text(encoding="utf-8")
        for name in ("events.jsonl", "stderr.log")
        if (run_dir / name).is_file()
    ]
    if parts:
        log_path.write_text("\n".join(parts), encoding="utf-8")
