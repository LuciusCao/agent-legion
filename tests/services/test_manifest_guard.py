"""入队 manifest 守卫的纯单元层用例（agent_broker/manifest_guard.py）。

#843 评审 P1（c 层）：expected output 命中结果归档保留成员名（result.json
/ node.log / result-output-artifacts.json）在入队即拒——该形态会在归档与
提升面与协议成员碰撞（v2 元数据换写吞掉真产物、node.log 与捕获日志双写
互覆）。不触库（纯函数）；#13 路由性守卫（require_routable_execution）的
既有回归由调用方契约与 CI 路由面覆盖。
"""

from __future__ import annotations

import pytest

from server.app.agent_broker.manifest_guard import require_unreserved_output_names

pytestmark = pytest.mark.no_db

_RESERVED = ("result.json", "node.log", "result-output-artifacts.json")


@pytest.mark.parametrize("name", _RESERVED)
def test_reserved_output_name_rejected_with_actionable_message(name: str) -> None:
    """三个保留名逐一点名拒绝：错误信息带冲突名与改法（重命名产物）。"""
    with pytest.raises(ValueError, match="reserved result-archive member.*rename"):
        require_unreserved_output_names({"expected_outputs": [name]})


def test_all_reserved_names_rejected_together() -> None:
    manifest = {"expected_outputs": ["out.json", *_RESERVED]}
    with pytest.raises(ValueError) as excinfo:
        require_unreserved_output_names(manifest)
    assert "result.json" in str(excinfo.value)


def test_normal_and_nested_output_names_pass() -> None:
    """合法名不受影响：嵌套声明名 reports/final.json（#631 祝福形态——
    归一化后是独立路径）与非 JSON 产物名（output.txt；result.txt 是
    result.json 的近邻名但不同名——守卫按归一化名字同一性判定，非
    模式/子串匹配，.txt 不因形近被误伤）。"""
    require_unreserved_output_names(
        {"expected_outputs": ["out.json", "result-summary.json", "reports/final.json"]}
    )
    require_unreserved_output_names({"expected_outputs": ["output.txt", "result.txt"]})


def test_alias_spellings_of_reserved_names_rejected() -> None:
    """#1164 收口：保留名的别名拼写（``./result.json``、``.//result.json``）
    按归一化形态拒绝——它们与 ``result.json`` 是同一落盘路径（提升守卫的
    source 落点即归一化形态），原字符串精确比对放行后 staging 的元数据
    成员会被静默提升成产物。嵌套路径（``sub/result.json``）是独立路径、
    合法形态，不误伤（精确语义而非前缀匹配）。"""
    for name in ("./result.json", ".//result.json", "./node.log"):
        with pytest.raises(ValueError, match="reserved result-archive member.*rename"):
            require_unreserved_output_names({"expected_outputs": [name]})
    require_unreserved_output_names({"expected_outputs": ["sub/result.json"]})


def test_unsafe_spellings_skip_reserved_check_not_crash() -> None:
    """绝对 / ``..`` 形态不在此判（unsafe 家族由提升守卫的既有拒绝收口），
    归一化路径检查对它们只跳过比对、不抛不拦。"""
    require_unreserved_output_names({"expected_outputs": ["/result.json", "sub/../result.json"]})


def test_missing_or_empty_outputs_pass() -> None:
    require_unreserved_output_names({})
    require_unreserved_output_names({"expected_outputs": []})
    require_unreserved_output_names({"expected_outputs": None})
