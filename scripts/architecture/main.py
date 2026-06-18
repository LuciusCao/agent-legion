from pathlib import Path

from scripts.architecture.repository import check_repository


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    errors = check_repository(root)
    for error in errors:
        print(f"ERROR: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
