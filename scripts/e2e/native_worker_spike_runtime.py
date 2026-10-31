from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def materialize_python_runtime(release_repo: Path, install_root: Path) -> None:
    venv = release_repo / ".venv"
    python_real = (venv / "bin" / "python").resolve(strict=True)
    runtime = install_root / "python"
    shutil.copytree(python_real.parent.parent, runtime)
    runtime_python = runtime / "bin" / python_real.name
    if not runtime_python.is_file() or not os.access(runtime_python, os.X_OK):
        raise RuntimeError(f"self-contained Python is not executable: {runtime_python}")
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for name in ("python", "python3", version):
        link = venv / "bin" / name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(runtime_python)
    config = venv / "pyvenv.cfg"
    lines = [
        line
        for line in config.read_text(encoding="utf-8").splitlines()
        if not line.startswith("home = ")
    ]
    config.write_text(f"home = {runtime / 'bin'}\n" + "\n".join(lines) + "\n", encoding="utf-8")


def secure_release(install_root: Path) -> None:
    install_root.chmod(0o755)
    for path in install_root.rglob("*"):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod((mode & 0o111) | 0o444)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-repo", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    args = parser.parse_args()
    materialize_python_runtime(args.release_repo, args.install_root)
    secure_release(args.install_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
