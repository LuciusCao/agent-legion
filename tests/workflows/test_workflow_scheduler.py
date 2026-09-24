import json
from pathlib import Path

from server.app.workflows.conditions import condition_matches, selected_edges
from server.app.workflows.definition import (
    WorkflowCondition,
    WorkflowEdge,
    load_workflow_definition,
)
from server.app.workflows.scheduler import (
    find_ready_nodes,
    summarize_job_status,
)
from server.app.workflows.workflow_branching import (
    downstream_nodes,
    evaluate_branches,
)
from tests.helpers import load_builtin_definition


def _definition():
    return load_builtin_definition("education_video_problems_generation")


def test_find_ready_nodes_starts_with_root(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["intake_knowledge_points"]


def test_find_ready_nodes_requires_inputs(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["intake_knowledge_points"] = "completed"

    assert find_ready_nodes(definition, nodes, artifact_dir=tmp_path) == []

    (tmp_path / "knowledge_point.json").write_text("{}", encoding="utf-8")
    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["write_script", "generate_questions"]


def test_parallel_ready_nodes_after_intake(tmp_path):
    definition = _definition()
    nodes = {key: "pending" for key in definition.nodes}
    nodes["intake_knowledge_points"] = "completed"
    nodes["write_script"] = "completed"
    nodes["generate_questions"] = "completed"
    (tmp_path / "knowledge_point.json").write_text("{}", encoding="utf-8")
    (tmp_path / "script.md").write_text("x", encoding="utf-8")
    (tmp_path / "exercises.json").write_text("{}", encoding="utf-8")

    ready = find_ready_nodes(definition, nodes, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["review_script", "review_questions"]


def test_downstream_nodes_are_recursive():
    definition = _definition()

    downstream = downstream_nodes(definition, "write_script")

    assert "review_script" in downstream
    assert "publish_content" in downstream
    assert "generate_questions" not in downstream


def test_summarize_job_status():
    assert summarize_job_status([]) == "queued"
    assert summarize_job_status(["pending", "pending"]) == "queued"
    assert summarize_job_status(["completed", "running"]) == "running"
    assert summarize_job_status(["running", "failed"]) == "running"
    assert summarize_job_status(["completed", "failed"]) == "failed"
    assert summarize_job_status(["completed", "completed"]) == "completed"
    assert summarize_job_status(["completed", "stale"]) == "queued"


def test_condition_matches_artifact_json_path(tmp_path):
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False, "reason_code": "pure_calculation"}),
        encoding="utf-8",
    )

    condition = WorkflowCondition(
        artifact="decision.json",
        path="$.eligible",
        equals=False,
    )

    assert condition_matches(condition, tmp_path) is True


def test_condition_missing_artifact_is_not_match(tmp_path):
    condition = WorkflowCondition(
        artifact="decision.json",
        path="$.eligible",
        equals=True,
    )

    assert condition_matches(condition, tmp_path) is False


def test_selected_edges_filters_conditions(tmp_path):
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": True}),
        encoding="utf-8",
    )
    edges = [
        WorkflowEdge(source="classify", target="good", condition=None),
        WorkflowEdge(
            source="classify",
            target="uploadable",
            condition=WorkflowCondition("decision.json", "$.eligible", True),
        ),
        WorkflowEdge(
            source="classify",
            target="non_uploadable",
            condition=WorkflowCondition("decision.json", "$.eligible", False),
        ),
    ]

    assert [edge.target for edge in selected_edges(edges, tmp_path)] == [
        "good",
        "uploadable",
    ]


def test_summarize_job_status_treats_not_applicable_as_terminal():
    assert summarize_job_status(["completed", "not_applicable"]) == "completed"
    assert summarize_job_status(["completed", "not_applicable", "failed"]) == "failed"
    assert summarize_job_status(["pending", "not_applicable"]) == "queued"


def _write_branching_definition(path: Path) -> None:
    path.write_text(
        """
key: branching
label: Branching
schema_version: 2
nodes:
  root:
    label: Root
    capability: root
  gate:
    label: Gate
    capability: gate
    after: [root]
  good:
    label: Good
    capability: good
    after: [gate]
  leaf:
    label: Leaf
    capability: leaf
    after: [good]
  skipped:
    label: Skipped
    capability: skipped
edges:
  - {from: gate, to: good, when: {artifact: decision.json, path: "$.eligible", equals: true}}
  - {from: gate, to: skipped, when: {artifact: decision.json, path: "$.eligible", equals: false}}
  - {from: good, to: leaf}
""",
        encoding="utf-8",
    )


def test_evaluate_branches_marks_unselected_branch_not_applicable(tmp_path):
    path = tmp_path / "branching.yaml"
    _write_branching_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False}),
        encoding="utf-8",
    )
    statuses = {key: "pending" for key in definition.nodes}
    statuses["root"] = "completed"
    statuses["gate"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable
    assert "leaf" in result.not_applicable
    assert "skipped" not in result.not_applicable


def test_evaluate_branches_marks_node_not_applicable_when_all_incoming_conditions_false(tmp_path):
    path = tmp_path / "branching.yaml"
    _write_branching_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(
        json.dumps({"eligible": False}),
        encoding="utf-8",
    )
    statuses = {key: "pending" for key in definition.nodes}
    statuses["gate"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable


def test_unconditional_fanout_is_not_marked_not_applicable(tmp_path):
    path = tmp_path / "fanout.yaml"
    path.write_text(
        """
key: fanout
label: Fanout
nodes:
  root:
    label: Root
    capability: root
  left:
    label: Left
    capability: left
    after: [root]
  right:
    label: Right
    capability: right
    after: [root]
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    statuses = {key: "pending" for key in definition.nodes}
    statuses["root"] = "completed"

    result = evaluate_branches(definition, statuses, tmp_path)

    assert result.not_applicable == set()


def test_start_node_is_treated_as_completed_and_never_ready(tmp_path):
    """EXEC-WORKFLOW-START-001: the demo DAG has an explicit start node; it never
    enters the ready set and its outgoing edge is always satisfied."""
    definition = _definition()
    # Simulate a real job: job_nodes only ever cover executable nodes, so the
    # start node is absent from the status map entirely.
    statuses = {key: "pending" for key in definition.executable_nodes}

    ready = find_ready_nodes(definition, statuses, artifact_dir=tmp_path)

    assert [node.key for node in ready] == ["intake_knowledge_points"]
    assert definition.start_node is not None
    assert definition.start_node.key not in statuses


def test_legacy_snapshot_without_start_keeps_readiness_behavior(tmp_path):
    """D3 regression: a pre-start definition (no start in the snapshot) gets a
    synthetic start injected; root readiness is unchanged."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """
key: legacy
label: Legacy
schema_version: 2
nodes:
  root:
    label: Root
    capability: root
  child:
    label: Child
    capability: child
edges:
  - {from: root, to: child}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    assert definition.start_node is not None
    # job_nodes for a legacy in-flight job hold only the business nodes.
    statuses = {"root": "pending", "child": "pending"}

    assert [node.key for node in find_ready_nodes(definition, statuses, tmp_path)] == ["root"]

    statuses["root"] = "completed"
    assert [node.key for node in find_ready_nodes(definition, statuses, tmp_path)] == ["child"]


def test_evaluate_branches_treats_injected_start_as_completed(tmp_path):
    """The injected start's unconditional outgoing edges never mark targets
    not_applicable, even though the start has no job_nodes row."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """
key: legacy
label: Legacy
nodes:
  root:
    capability: root
  child:
    capability: child
    after: [root]
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)

    result = evaluate_branches(definition, {"root": "pending", "child": "pending"}, tmp_path)

    assert result.not_applicable == set()


def test_allowed_nodes_exclude_start() -> None:
    from server.app.workflows.execution_control import allowed_nodes

    definition = _definition()
    start_key = definition.start_node.key

    full = allowed_nodes(definition, {"execution_mode": "full"})
    assert start_key not in full
    assert set(full) == set(definition.executable_nodes)

    until = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "review_script"},
    )
    assert start_key not in until
    assert until == frozenset({"intake_knowledge_points", "write_script", "review_script"})


def _implicit_definition():
    from server.app.workflows.definition import (
        WorkflowDefinition,
        WorkflowIntake,
        WorkflowNode,
    )

    return WorkflowDefinition(
        key="implicit",
        label="implicit",
        intake=WorkflowIntake(),
        nodes={
            "p": WorkflowNode(
                key="p", label="P", capability="p", inputs=["x.json"], outputs=["x.json"]
            ),
            "q": WorkflowNode(
                key="q", label="Q", capability="q", inputs=["x.json"], outputs=["y.json"]
            ),
        },
    )


def test_find_ready_nodes_blocks_implicit_consumer_until_producer_finishes(tmp_path):
    """#759 codex P1：RMW 产物重置后被刻意保留，文件在不代表已重写——隐式
    消费者必须等生产者完成，否则两者并发重跑、消费者读到旧值。突变自检
    锚点：p、q 间无显式边，屏障只能来自隐式消费边。"""
    definition = _implicit_definition()
    (tmp_path / "x.json").write_text("old", encoding="utf-8")
    statuses = {"p": "pending", "q": "stale"}

    ready = find_ready_nodes(definition, statuses, artifact_dir=tmp_path)
    assert [node.key for node in ready] == ["p"]

    statuses["p"] = "completed"
    ready = find_ready_nodes(definition, statuses, artifact_dir=tmp_path)
    assert [node.key for node in ready] == ["q"]


def test_find_ready_nodes_implicit_barrier_ignores_terminal_producers(tmp_path):
    """completed / not_applicable 生产者不设障（not_applicable 的产物本轮
    不刷新，读既有文件与文件存在语义一致）。"""
    definition = _implicit_definition()
    (tmp_path / "x.json").write_text("old", encoding="utf-8")

    statuses = {"p": "not_applicable", "q": "pending"}
    ready = find_ready_nodes(definition, statuses, artifact_dir=tmp_path)
    assert [node.key for node in ready] == ["q"]


def test_find_ready_nodes_rmw_self_production_does_not_block(tmp_path):
    """RMW 自身回传不构成自障（p 输入与输出同名，自己不等自己）。"""
    definition = _implicit_definition()
    (tmp_path / "x.json").write_text("old", encoding="utf-8")
    statuses = {"p": "pending", "q": "completed"}

    ready = find_ready_nodes(definition, statuses, artifact_dir=tmp_path)
    assert [node.key for node in ready] == ["p"]


def test_find_ready_nodes_implicit_cycle_fails_closed(tmp_path):
    """隐式边成环且双方产物都在：互堵停住（fail-closed），不静默并发读
    旧值——环本来就没有正确顺序，停住是操作员可见的。"""
    from server.app.workflows.definition import (
        WorkflowDefinition,
        WorkflowIntake,
        WorkflowNode,
    )

    definition = WorkflowDefinition(
        key="cycle",
        label="cycle",
        intake=WorkflowIntake(),
        nodes={
            "p": WorkflowNode(
                key="p", label="P", capability="p", inputs=["y.json"], outputs=["x.json"]
            ),
            "q": WorkflowNode(
                key="q", label="Q", capability="q", inputs=["x.json"], outputs=["y.json"]
            ),
        },
    )
    (tmp_path / "x.json").write_text("old", encoding="utf-8")
    (tmp_path / "y.json").write_text("old", encoding="utf-8")
    statuses = {"p": "pending", "q": "pending"}

    assert find_ready_nodes(definition, statuses, artifact_dir=tmp_path) == []


# ---------------------------------------------------------------------------
# ③ 759 对抗复审 P1：条件产物生产者屏障（condition_producer_in_flight）
# ---------------------------------------------------------------------------


def _write_condition_producer_definition(path: Path) -> None:
    """条件产物生产者（scorer）与分支源（gate）不相邻：重跑 scorer 时 gate
    保持 completed——生产者屏障必须挡住「缺失/旧字节当判定」。"""
    path.write_text(
        """
key: cond_producer
label: t
schema_version: 2
nodes:
  root:
    label: Root
    capability: root
  scorer:
    label: Scorer
    capability: score
    after: [root]
    outputs: [decision.json]
  gate:
    label: Gate
    capability: gate
    after: [root]
  good:
    label: Good
    capability: good
    after: [gate]
  skipped:
    label: Skipped
    capability: skipped
edges:
  - {from: gate, to: good, when: {artifact: decision.json, path: "$.eligible", equals: true}}
  - {from: gate, to: skipped, when: {artifact: decision.json, path: "$.eligible", equals: false}}
""",
        encoding="utf-8",
    )


def test_evaluate_branches_defers_when_condition_producer_in_flight(tmp_path):
    """生产者在途（重跑中、条件文件被暂存删除）时，分支裁决推迟——good/
    skipped 都不标 not_applicable；生产者完成后按新字节恢复裁决。"""
    path = tmp_path / "wf.yaml"
    _write_condition_producer_definition(path)
    definition = load_workflow_definition(path)
    statuses = {key: "pending" for key in definition.nodes}
    statuses.update({"root": "completed", "gate": "completed"})  # scorer 在途

    result = evaluate_branches(definition, statuses, tmp_path)
    assert result.not_applicable == set()  # 推迟：不标任何 not_applicable

    (tmp_path / "decision.json").write_text(json.dumps({"eligible": False}), encoding="utf-8")
    statuses["scorer"] = "completed"
    result = evaluate_branches(definition, statuses, tmp_path)
    assert "good" in result.not_applicable  # 裁决恢复：新字节选定 skipped 支
    assert "skipped" not in result.not_applicable


def test_evaluate_branches_does_not_defer_for_terminal_producer(tmp_path):
    """not_applicable 生产者不设障：其产物本轮不刷新，读既有文件与文件存
    在语义一致（与调度侧隐式生产者屏障同纪律）。"""
    path = tmp_path / "wf.yaml"
    _write_condition_producer_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(json.dumps({"eligible": False}), encoding="utf-8")
    statuses = {key: "pending" for key in definition.nodes}
    statuses.update({"root": "completed", "gate": "completed", "scorer": "not_applicable"})

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable  # 裁决照常进行
    assert "skipped" not in result.not_applicable


def test_find_ready_nodes_defers_when_condition_producer_in_flight(tmp_path):
    """RMW/保留旧字节面：条件文件在场（选中 good）但生产者在途——good 不
    就绪（等生产者重跑后按新字节重评）；生产者完成后就绪。"""
    path = tmp_path / "wf.yaml"
    _write_condition_producer_definition(path)
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(json.dumps({"eligible": True}), encoding="utf-8")
    statuses = {key: "pending" for key in definition.nodes}
    statuses.update({"root": "completed", "gate": "completed", "scorer": "pending"})

    ready = {node.key for node in find_ready_nodes(definition, statuses, tmp_path)}
    assert "good" not in ready

    statuses["scorer"] = "completed"
    ready = {node.key for node in find_ready_nodes(definition, statuses, tmp_path)}
    assert "good" in ready


# ---------------------------------------------------------------------------
# ③ 759 二轮对抗复审：自门控排除集 + 多源汇合的推迟减法
# ---------------------------------------------------------------------------


def test_self_produced_condition_does_not_deadlock(tmp_path):
    """条件产物由被门控 target 自己生产（loader 允许的合法定义）：自门控
    生产者本来就跑不到（等分支被选中），对它设障是循环等待——排除集让
    这类定义按文件语义评估（缺失即 false），与屏障引入前一致。"""
    path = tmp_path / "self_gated.yaml"
    path.write_text(
        """
key: self_gated
label: t
schema_version: 2
nodes:
  gate:
    label: Gate
    capability: gate
  worker:
    label: Worker
    capability: worker
    after: [gate]
    outputs: [progress.json]
edges:
  - {from: gate, to: worker, when: {artifact: progress.json, path: "$.ok", equals: true}}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    statuses = {"gate": "completed", "worker": "pending"}

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "worker" in result.not_applicable  # 文件缺失即 false：可终止（不死锁）


def test_producer_inside_gated_branch_does_not_deadlock(tmp_path):
    """条件产物由被门控分支内部节点生产（gate→good 条件、good→reporter、
    reporter 产 decision.json）：同样按文件语义评估，不永久推迟。"""
    path = tmp_path / "inner.yaml"
    path.write_text(
        """
key: inner
label: t
schema_version: 2
nodes:
  gate:
    label: Gate
    capability: gate
  good:
    label: Good
    capability: good
    after: [gate]
  reporter:
    label: Reporter
    capability: reporter
    after: [good]
    outputs: [decision.json]
edges:
  - {from: gate, to: good, when: {artifact: decision.json, path: "$.eligible", equals: true}}
  - {from: good, to: reporter}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    statuses = {"gate": "completed", "good": "pending", "reporter": "pending"}

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable
    assert "reporter" in result.not_applicable


def test_mixed_producers_only_external_ones_gate(tmp_path):
    """混合生产者：外部生产者已终态 + 分支内生产者在途——外部不挡、内部
    被排除，裁决照常（按在场文件）。"""
    path = tmp_path / "mixed.yaml"
    path.write_text(
        """
key: mixed
label: t
schema_version: 2
nodes:
  root:
    label: Root
    capability: root
  scorer:
    label: Scorer
    capability: score
    after: [root]
    outputs: [decision.json]
  gate:
    label: Gate
    capability: gate
    after: [root]
  good:
    label: Good
    capability: good
    after: [gate]
  reporter:
    label: Reporter
    capability: reporter
    after: [good]
    outputs: [decision.json]
edges:
  - {from: gate, to: good, when: {artifact: decision.json, path: "$.eligible", equals: true}}
  - {from: good, to: reporter}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    (tmp_path / "decision.json").write_text(json.dumps({"eligible": False}), encoding="utf-8")
    statuses = {key: "pending" for key in definition.nodes}
    statuses.update(
        {"root": "completed", "gate": "completed", "scorer": "completed"}  # reporter 在途
    )

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "good" in result.not_applicable  # 外部已终态 + 内部被排除：按文件裁决
    assert "reporter" in result.not_applicable


def test_deferred_source_target_survives_other_sources_verdict(tmp_path):
    """多源汇合：x 有两条条件入边——a→x（生产者 p1 在途，a 推迟）与 b→x
    （p2 已终态、条件为假，b 正常评估）。推迟 source 的 target 不得被其他
    source 的评估钉成 not_applicable（a 之后仍可能选中它）。"""
    path = tmp_path / "confluence.yaml"
    path.write_text(
        """
key: confluence
label: t
schema_version: 2
nodes:
  p1:
    label: P1
    capability: p1
    outputs: [c1.json]
  p2:
    label: P2
    capability: p2
    outputs: [c2.json]
  a:
    label: A
    capability: a
  b:
    label: B
    capability: b
  x:
    label: X
    capability: x
edges:
  - {from: a, to: x, when: {artifact: c1.json, path: "$.ok", equals: true}}
  - {from: b, to: x, when: {artifact: c2.json, path: "$.ok", equals: true}}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    (tmp_path / "c2.json").write_text(json.dumps({"ok": False}), encoding="utf-8")
    statuses = {
        "p1": "pending",  # a 的条件生产者在途 → a 推迟
        "p2": "completed",
        "a": "completed",
        "b": "completed",
        "x": "pending",
    }

    result = evaluate_branches(definition, statuses, tmp_path)

    assert result.not_applicable == set()  # x 既未被 a 选中评估、也不被 b 钉死


def test_find_ready_nodes_self_gated_producer_does_not_block(tmp_path):
    """就绪侧同纪律：target 自产条件产物且文件在场时，自门控生产者在途
    不设障——worker 按文件语义就绪（不永久卡住）。"""
    path = tmp_path / "self_gated.yaml"
    path.write_text(
        """
key: self_gated
label: t
schema_version: 2
nodes:
  gate:
    label: Gate
    capability: gate
  worker:
    label: Worker
    capability: worker
    after: [gate]
    outputs: [progress.json]
edges:
  - {from: gate, to: worker, when: {artifact: progress.json, path: "$.ok", equals: true}}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    (tmp_path / "progress.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    statuses = {"gate": "completed", "worker": "pending"}

    ready = {node.key for node in find_ready_nodes(definition, statuses, tmp_path)}

    assert "worker" in ready


def test_producer_implicit_downstream_of_target_does_not_deadlock(tmp_path):
    """③ 三轮对抗复审 P1：条件产物由 target 的**隐式**下游生产（probe 消费
    target 的 output、产出 gate→target 的条件；loader 不要求 inputs 相邻，
    无显式边）——排除集必须是合并闭包（显式 ∪ 隐式消费边），否则 probe
    永远跑不到（等 target 的 output），对它设障是循环等待（永久静默挂
    起）。修复后按文件语义评估（缺失即 false），可终止。"""
    path = tmp_path / "implicit_self_gated.yaml"
    path.write_text(
        """
key: implicit_self_gated
label: t
schema_version: 2
nodes:
  gate:
    label: Gate
    capability: gate
  target:
    label: Target
    capability: target
    after: [gate]
    outputs: [out.json]
  probe:
    label: Probe
    capability: probe
    inputs: [out.json]
    outputs: [progress.json]
edges:
  - {from: gate, to: target, when: {artifact: progress.json, path: "$.ok", equals: true}}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    statuses = {"gate": "completed", "target": "pending", "probe": "pending"}

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "target" in result.not_applicable  # 合并闭包排除 probe：按文件语义可终止


def test_sibling_branch_producer_does_not_hold_decidable_edges_hostage(tmp_path):
    """③ 终审 P1：同 source 两条条件边——a 边条件产物外部缺失（可判定为
    假），b 边条件产物由 a 分支内的 probe 生产（在途）。逐边推迟：a 支当
    轮钉死（probe 随之终态），b 推迟——下一轮 b 按语义收尾。整源推迟
    （修复前）把可判定的 a 边挟持住：a 不钉死 → probe 永不跑 → 屏障永不
    解除，job 永久静默挂起（基线行为可终止）。"""
    path = tmp_path / "sibling.yaml"
    path.write_text(
        """
key: sibling
label: t
schema_version: 2
nodes:
  gate:
    label: Gate
    capability: gate
  a:
    label: A
    capability: a
    after: [gate]
  b:
    label: B
    capability: b
    after: [gate]
  probe:
    label: Probe
    capability: probe
    outputs: [d.json]
edges:
  - {from: gate, to: a, when: {artifact: c.json, path: "$.ok", equals: true}}
  - {from: gate, to: b, when: {artifact: d.json, path: "$.ok", equals: true}}
  - {from: a, to: probe}
""",
        encoding="utf-8",
    )
    definition = load_workflow_definition(path)
    # c.json 缺失（外部产物、无生产者）：a 边可判定为假；d.json 由 a 分支内
    # 的 probe 生产且在途：b 边推迟。
    statuses = {"gate": "completed", "a": "pending", "b": "pending", "probe": "pending"}

    result = evaluate_branches(definition, statuses, tmp_path)

    assert "a" in result.not_applicable  # 可判定的兄弟边照常裁决（钉死）
    assert "probe" in result.not_applicable  # a 的下游随钉（probe 终态）
    assert "b" not in result.not_applicable  # 在途边推迟：不标

    # probe 终态后下一轮：b 边按文件语义裁决（缺失即假）——job 可终止。
    statuses.update({"a": "not_applicable", "probe": "not_applicable"})
    result = evaluate_branches(definition, statuses, tmp_path)
    assert "b" in result.not_applicable
