from pathlib import Path


def write_neutral_budget_governance(root: Path) -> None:
    config = root / "config/architecture"
    config.mkdir(parents=True, exist_ok=True)
    (root / "budget-fixtures").mkdir(exist_ok=True)
    (root / "budget-tests").mkdir(exist_ok=True)
    (config / "architecture-budget-policy.yaml").write_text(
        "version: 1\n"
        "production:\n  roots:\n"
        "    - path: budget-fixtures\n"
        "      extensions: [.py]\n"
        "  exclude: []\n"
        "  buffer_lines: 5\n"
        "  max_lines: 800\n"
        "tests:\n  roots:\n"
        "    - path: budget-tests\n"
        "      patterns: ['**/*.py']\n"
        "  max_lines: 1000\n",
        encoding="utf-8",
    )
    (config / "architecture-budgets.json").write_text(
        '{\n  "version": 2,\n  "files": {}\n}\n', encoding="utf-8"
    )
