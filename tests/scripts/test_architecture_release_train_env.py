"""The quality-gate release-train opt-out must evaluate to '1' or '0'.

The env value comes from a GitHub Actions expression. GHA operator
precedence (``&&`` binds tighter than ``||``, and both return their operand
rather than a boolean) means an unparenthesised ``A || B && '1' || '0'``
short-circuits arm ``A`` to a bare boolean ``true`` — the guard checks
``== "1"`` and the opt-out silently never fires. That is exactly how PR
#460 failed: a legitimately re-filed exemption (108 → 111, reviewed in its
own PR) was rejected against main's lagging floor. These tests parse the
committed workflow and evaluate its expression under GHA semantics for
every triggering context, so the precedence regression cannot return.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality-gate.yml"
ENV_NAME = "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN"

# (event, base_ref, head_ref, ref) → expected env value. The four gates
# the expression encodes: release-train PR, main/master push rerun,
# feature→develop PR, and anything else.
_CONTEXTS: dict[str, tuple[dict[str, Any], str]] = {
    "release_train_pr": (
        {"event_name": "pull_request", "base_ref": "main", "head_ref": "develop", "ref": ""},
        "1",
    ),
    "main_push_rerun": (
        {"event_name": "push", "base_ref": "", "head_ref": "", "ref": "refs/heads/main"},
        "1",
    ),
    "master_push_rerun": (
        {"event_name": "push", "base_ref": "", "head_ref": "", "ref": "refs/heads/master"},
        "1",
    ),
    "feature_pr": (
        {
            "event_name": "pull_request",
            "base_ref": "develop",
            "head_ref": "fix/some-bug",
            "ref": "",
        },
        "0",
    ),
    "develop_push": (
        {"event_name": "push", "base_ref": "", "head_ref": "", "ref": "refs/heads/develop"},
        "0",
    ),
    "hotfix_pr": (
        {
            "event_name": "pull_request",
            "base_ref": "main",
            "head_ref": "fix/prod-hotfix",
            "ref": "",
        },
        "0",
    ),
}


def _release_train_expression() -> str:
    """Raw ``${{ ... }}`` body of the release-train env in the workflow."""
    raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in raw.get("jobs", {}).values():
        for step in job.get("steps", []):
            value = step.get("env", {}).get(ENV_NAME)
            if value:
                match = re.fullmatch(r"\$\{\{(.*)\}\}", str(value).strip())
                assert match is not None, f"{ENV_NAME} is not an expression: {value!r}"
                return match.group(1).strip()
    pytest.fail(f"{ENV_NAME} not found in {WORKFLOW}")


class _GhaExpr:
    """Minimal GitHub Actions expression evaluator.

    Implements exactly the precedence and truthiness rules this env relies
    on: ``&&`` binds tighter than ``||``, both short-circuit, and both
    return the chosen operand itself (so ``cond && '1' || '0'`` is the
    canonical ternary). Comparison is ``==`` / ``!=`` on scalars. It is a
    test double, not a general evaluator: any other operator raises.
    """

    def __init__(self, context: dict[str, Any]) -> None:
        self._context = context

    def evaluate(self, text: str) -> Any:
        or_arms = self._split(text, "||")
        result: Any = None
        for arm in or_arms:
            result = self._eval_and(arm)
            if self._truthy(result):
                return result
        return result  # All falsy: GHA returns the final operand as-is.

    def _eval_and(self, text: str) -> Any:
        and_arms = self._split(text, "&&")
        result: Any = None
        for arm in and_arms:
            result = self._atom(arm)
            if not self._truthy(result):
                return result  # && returns its first falsy operand.
        return result

    def _split(self, text: str, operator: str) -> list[str]:
        parts, depth, quote, current = [], 0, "", []
        i = 0
        while i < len(text):
            char = text[i]
            if quote:
                current.append(char)
                if char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
                current.append(char)
            elif char == "(":
                depth += 1
                current.append(char)
            elif char == ")":
                depth -= 1
                current.append(char)
            elif depth == 0 and self._at_operator(text, i, operator):
                parts.append("".join(current))
                current = []
                i += len(operator)
            else:
                current.append(char)
            i += 1
        parts.append("".join(current))
        return parts

    @staticmethod
    def _at_operator(text: str, i: int, operator: str) -> bool:
        """A top-level operator token: spaced on both sides, per the file."""
        return (
            text[i : i + len(operator)] == operator
            and (i == 0 or text[i - 1] == " ")
            and text[i + len(operator) : i + len(operator) + 1] == " "
        )

    @staticmethod
    def _truthy(value: Any) -> bool:
        return bool(value)

    def _atom(self, text: str) -> Any:
        text = text.strip()
        if not text:
            raise ValueError("empty expression arm")
        if text.startswith("(") and text.endswith(")"):
            return self.evaluate(text[1:-1])
        if text[0] in "'\"":
            return text[1:-1]
        for op in ("==", "!="):
            if op in text:
                left, right = (side.strip() for side in text.split(op, 1))
                lhs, rhs = self._atom(left), self._atom(right)
                return lhs == rhs if op == "==" else lhs != rhs
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", text):
            return self._context.get(text.split(".", 1)[1], None)
        if text == "true":
            return True
        if text == "false":
            return False
        raise ValueError(f"unsupported atom: {text!r}")


@pytest.mark.parametrize(
    ("context", "expected"),
    [(ctx, value) for _name, (ctx, value) in _CONTEXTS.items()],
    ids=list(_CONTEXTS),
)
def test_release_train_env_evaluates_under_gha_precedence(
    context: dict[str, Any], expected: str
) -> None:
    expression = _release_train_expression()
    assert _GhaExpr(context).evaluate(expression) == expected
