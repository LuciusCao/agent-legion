#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def cleanup_videos_tree(videos_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    from server.app.pipeline.agent_workspace import cleanup_agent_workspace_files

    polluted_dirs = 0
    removed_count = 0
    for video_dir in sorted(path for path in videos_dir.iterdir() if path.is_dir()):
        if dry_run:
            removed = [
                video_dir / name
                for name in [
                    "AGENTS.md",
                    "BOOTSTRAP.md",
                    "HEARTBEAT.md",
                    "IDENTITY.md",
                    "MEMORY.md",
                    "SOUL.md",
                    "TOOLS.md",
                    "USER.md",
                    ".openclaw",
                    "memory",
                ]
                if (video_dir / name).exists()
            ]
        else:
            removed = cleanup_agent_workspace_files(video_dir)
        if removed:
            polluted_dirs += 1
            removed_count += len(removed)
            for path in removed:
                print(path)
    return polluted_dirs, removed_count


def main() -> int:
    from server.app.settings import load_settings

    parser = argparse.ArgumentParser(
        description="Remove OpenClaw agent workspace pollution from video directories."
    )
    parser.add_argument(
        "--videos-dir",
        type=Path,
        default=None,
        help="Directory containing per-video artifact folders. Defaults to configured data/videos.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files without deleting them.")
    args = parser.parse_args()

    settings = load_settings()
    videos_dir = args.videos_dir or settings.videos_dir
    if not videos_dir.exists():
        print(f"videos directory does not exist: {videos_dir}")
        return 1

    polluted_dirs, removed_count = cleanup_videos_tree(videos_dir, dry_run=args.dry_run)
    action = "would remove" if args.dry_run else "removed"
    print(f"{action} {removed_count} entries from {polluted_dirs} video directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
