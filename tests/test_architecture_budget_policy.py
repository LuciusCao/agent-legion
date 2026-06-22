"""Tests for the source-file budget policy loader."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from scripts.architecture.budget_policy import (
    BudgetConfigurationError,
    BudgetPolicy,
    ProductionRoot,
    TestRoot,
    load_budget_policy,
)

VALID_POLICY = {
    "version": 1,
    "production": {
        "roots": [
            {"path": "server/app", "extensions": [".py"]},
        ],
        "exclude": [],
        "buffer_lines": 5,
    },
    "tests": {
        "roots": [
            {"path": "tests", "patterns": ["**/*.py"]},
        ],
        "max_lines": 1000,
    },
}


def write_policy(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


class TestBudgetPolicyLoader:
    def test_valid_policy_loads_correctly(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        write_policy(policy_path, VALID_POLICY)

        policy = load_budget_policy(policy_path)

        assert policy == BudgetPolicy(
            production_roots=(ProductionRoot(path="server/app", extensions=(".py",)),),
            production_exclude=(),
            buffer_lines=5,
            test_roots=(TestRoot(path="tests", patterns=("**/*.py",)),),
            test_max_lines=1000,
        )

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(tmp_path / "missing.yaml")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text("not: valid: yaml: [", encoding="utf-8")

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    def test_wrong_version_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["version"] = 2
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    def test_unknown_top_level_field_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["extra"] = "value"
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError, match="unknown field"):
            load_budget_policy(policy_path)

    def test_unknown_nested_field_under_production_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["extra"] = "value"
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError, match="unknown field"):
            load_budget_policy(policy_path)

    def test_unknown_nested_field_under_tests_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["tests"]["extra"] = "value"
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError, match="unknown field"):
            load_budget_policy(policy_path)

    def test_unknown_root_field_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["roots"][0]["extra"] = "value"
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError, match="unknown field"):
            load_budget_policy(policy_path)

    @pytest.mark.parametrize("field", ["buffer_lines", "max_lines"])
    def test_boolean_used_as_integer_raises(self, tmp_path: Path, field: str) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        if field == "buffer_lines":
            data["production"]["buffer_lines"] = True
        else:
            data["tests"]["max_lines"] = True
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    @pytest.mark.parametrize("field", ["buffer_lines", "max_lines"])
    def test_non_positive_limit_raises(self, tmp_path: Path, field: str) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        if field == "buffer_lines":
            data["production"]["buffer_lines"] = 0
        else:
            data["tests"]["max_lines"] = -1
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    @pytest.mark.parametrize(
        "bad_path",
        ["/absolute/path", "relative/../escape", "server/app/../other"],
    )
    def test_absolute_or_dotdot_path_raises(self, tmp_path: Path, bad_path: str) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["roots"][0]["path"] = bad_path
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    @pytest.mark.parametrize("field", ["extensions", "patterns"])
    def test_empty_extensions_or_patterns_raises(self, tmp_path: Path, field: str) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        if field == "extensions":
            data["production"]["roots"][0]["extensions"] = []
        else:
            data["tests"]["roots"][0]["patterns"] = []
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    def test_duplicate_production_roots_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["roots"] = [
            {"path": "server/app", "extensions": [".py"]},
            {"path": "server/app/", "extensions": [".pyi"]},
        ]
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    def test_duplicate_test_roots_raises(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["tests"]["roots"] = [
            {"path": "tests", "patterns": ["**/*.py"]},
            {"path": "tests/", "patterns": ["**/*.py"]},
        ]
        write_policy(policy_path, data)

        with pytest.raises(BudgetConfigurationError):
            load_budget_policy(policy_path)

    def test_exclude_entries_are_normalized(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        data = copy.deepcopy(VALID_POLICY)
        data["production"]["exclude"] = ["server/app/generated"]
        write_policy(policy_path, data)

        policy = load_budget_policy(policy_path)

        assert policy.production_exclude == ("server/app/generated",)
