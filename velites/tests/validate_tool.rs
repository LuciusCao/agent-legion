//! Validate tool tests (#443, contract location migrated in #542): the tool
//! states (contract ok / violations / no contract / parse error) for both
//! the normative root `contract.yaml` and the deprecated embedded block,
//! and the agent-loop "not enabled" path when `validate` is absent from
//! `--tools`.

use std::path::{Path, PathBuf};

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &Path, skill_dirs: &[PathBuf]) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // These tests exercise contract semantics, not confinement (same
        // rationale as tests/bash_tool.rs).
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: skill_dirs.to_vec(),
    }
}

fn result_text(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

const _CONTRACT_BODY: &str =
    "files:\n  - path: script.md\n    format: text\n    min_chars: 10\n    required_headings: [\"## 目标\"]\n";

/// Skill dir with a one-file text contract in the normative root location
/// (min_chars 10, heading `## 目标`).
fn contract_skill(dir: &Path) -> PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(skill.join("contract.yaml"), _CONTRACT_BODY).unwrap();
    skill
}

/// Same contract, but embedded in the deprecated references/output-contract.md.
fn embedded_contract_skill(dir: &Path) -> PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        format!("# Contract\n\n```yaml contract\n{_CONTRACT_BODY}```\n"),
    )
    .unwrap();
    skill
}

#[tokio::test]
async fn validate_reports_ok_when_the_contract_holds() {
    let dir = tempfile::tempdir().unwrap();
    let skill = contract_skill(dir.path());
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "## 目标\nlong enough content").unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(!output.is_error);
    assert_eq!(result_text(&output), "contract ok (1 files checked)");
}

#[tokio::test]
async fn validate_lists_violations_as_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let skill = contract_skill(dir.path());
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error);
    let text = result_text(&output);
    assert!(text.starts_with("contract violations:\n"), "{text}");
    assert!(text.contains("1) script.md: missing required file"));
}

#[tokio::test]
async fn validate_without_contract_is_an_informational_error() {
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();

    // No --skill dirs at all, and a skill dir without a contract (root or
    // embedded) both land on the same "nothing to validate against" error.
    for dirs in [Vec::new(), vec![skill]] {
        let output = ToolKind::Validate
            .execute(&serde_json::json!({}), &ctx(&job, &dirs))
            .await;
        assert!(output.is_error);
        assert_eq!(
            result_text(&output),
            "no output contract found in the skill directories \
             (no contract.yaml and no embedded contract block); \
             nothing to validate against"
        );
    }
}

#[tokio::test]
async fn validate_surfaces_contract_parse_errors() {
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "```yaml contract\nfiles: [\n```\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error);
    assert!(result_text(&output).starts_with("contract parse error:"));
}

// --- #542: root contract.yaml three-tier resolution ---

#[tokio::test]
async fn validate_reports_the_deprecated_embedded_block_on_success() {
    // The embedded block still works, and the success message carries the
    // migration note (the agent can act on it, unlike the subcommand's
    // stdout-only signal).
    let dir = tempfile::tempdir().unwrap();
    let skill = embedded_contract_skill(dir.path());
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "## 目标\nlong enough content").unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(text.contains("contract ok (1 files checked)"), "{text}");
    assert!(text.contains("deprecated embedded block"), "{text}");
    assert!(text.contains("contract.yaml"), "{text}");
}

#[tokio::test]
async fn validate_root_contract_wins_over_the_embedded_block() {
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: root.md\n    format: text\n",
    )
    .unwrap();
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "```yaml contract\nfiles:\n  - path: embedded.md\n    format: text\n```\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("embedded.md"), "content").unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    // The root's file is the one missing: the embedded contract did not run
    // (its artifact exists; the violation names root.md only).
    assert!(output.is_error);
    let text = result_text(&output);
    assert!(text.contains("root.md: missing required file"), "{text}");
    assert!(!text.contains("embedded.md:"), "{text}");
    assert!(!text.contains("deprecated"), "{text}");
}

// --- #689 攻击报告 HIGH-1：契约引擎的第四读取面 ---
//
// 模型在运行中把契约声明的输出文件写成任意大小，end-of-run gate
// （--require-output 每次运行必执行）与 validate 工具（Forced tier）
// 都会触发 FileContract::check 的整文件读取——曾经是无界 fs::read。
// 现在与 read/json 共用同一有界读取与树预算；超限是诚实的 violation。

#[tokio::test]
async fn validate_reports_oversized_declared_file_as_a_violation() {
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: result.json\n    format: json\n    schema: {type: object}\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let mut content = vec![b'x'; 5 * 1024 * 1024];
    content.push(b'\n');
    std::fs::write(job.join("result.json"), &content).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error, "an over-cap declared file is a violation");
    let text = result_text(&output);
    assert!(
        text.contains("result.json: file is too large to validate"),
        "missing the honest too-large violation: {text}"
    );
    assert!(
        text.contains("4MB whole-file limit"),
        "the violation must name the limit: {text}"
    );
}

#[tokio::test]
async fn validate_reports_over_budget_json_trees_as_a_violation() {
    // A dense all-numbers file well under the 4 MiB byte cap but over the
    // 300k node budget: the tree-side rejection, not the byte-side one.
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: dense.json\n    format: json\n    schema: {type: object}\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let dense = format!("[{}]", vec!["1"; 300_001].join(","));
    std::fs::write(job.join("dense.json"), &dense).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error);
    let text = result_text(&output);
    assert!(
        text.contains("dense.json: file is too large to validate"),
        "missing the node-budget violation: {text}"
    );
    assert!(
        text.contains("300000 nodes"),
        "the violation must name the node budget: {text}"
    );
}

#[tokio::test]
async fn validate_caps_noisy_schema_violation_lists() {
    // A per-element schema error on a big-but-budgeted instance must stop
    // at 100 violations with an honest truncation note, not one row per
    // element. 5k nodes stays under the detailed-listing threshold, so this
    // pins the SMALL-instance per-item path (and its noise cap).
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: wide.json\n    format: json\n    schema:\n      type: array\n      items: {type: string}\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let wide = format!("[{}]", vec!["1"; 5_000].join(","));
    std::fs::write(job.join("wide.json"), &wide).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error);
    let text = result_text(&output);
    let count = text.lines().count() - 1; // first line is the header
    assert!(
        count <= 101,
        "the violation list must be capped at 100 plus the stop note, got {count} lines"
    );
    assert!(
        text.contains("schema violation at `/0`"),
        "small instances keep the per-item listing: {text}"
    );
    assert!(
        text.contains("schema validation stopped after 100 violations"),
        "missing the honest truncation note: {text}"
    );
}

#[tokio::test]
async fn validate_clips_noisy_schema_error_messages() {
    // allOf/contains violations embed the whole offending instance in the
    // error text; every violation message must be clipped (500 chars) before
    // it reaches the model or the Host failure record. 2k nodes stays under
    // the detailed-listing threshold, so the per-item path is the one
    // producing these messages.
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: big.json\n    format: json\n    schema:\n      allOf:\n        - contains: {const: 1}\n        - contains: {const: 2}\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let big: Vec<usize> = vec![0; 2000];
    std::fs::write(job.join("big.json"), serde_json::to_string(&big).unwrap()).unwrap();

    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(output.is_error);
    let text = result_text(&output);
    // Strip the "N) big.json: " list prefix and compare the message itself
    // against the 500-char clip plus its truncation-note suffix.
    for line in text.lines().skip(1) {
        let message = line.split_once(": ").map(|(_, rest)| rest).unwrap_or(line);
        if !message.starts_with("schema violation") {
            continue;
        }
        assert!(
            message.chars().count() <= 500 + 40,
            "violation message must be clipped, got {} chars",
            message.chars().count()
        );
        assert!(message.contains("[truncated"), "unclipped: {message}");
    }
}

#[tokio::test]
async fn validate_large_failing_instances_short_circuit_the_error_listing() {
    // #689 review MEDIUM-1: jsonschema's iter_errors eagerly collects every
    // error before the consumer-side 100-cap applies (a 300k-node
    // all-violating file transiently allocated 124-261 MiB debug). Past the
    // threshold, a FAILING check must short-circuit on the first error and
    // emit exactly one violation; a PASSING one must stay silent (is_valid
    // boolean fast path — the common case for a correct run).
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: big.json\n    format: json\n    schema:\n      type: array\n      items: {type: string}\n",
    )
    .unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    // 20k elements + 1 array container = 20,001 nodes > 10k threshold.
    let big = format!("[{}]", vec!["1"; 20_000].join(","));
    std::fs::write(job.join("big.json"), &big).unwrap();

    let output = ToolKind::Validate
        .execute(
            &serde_json::json!({}),
            &ctx(&job, std::slice::from_ref(&skill)),
        )
        .await;
    assert!(output.is_error);
    let text = result_text(&output);
    let count = text.lines().count() - 1; // first line is the header
    assert_eq!(count, 1, "one short-circuited violation, got: {text}");
    assert!(
        text.contains("schema validation failed (first violation at `/0`"),
        "the single violation must carry the first error and the fix guidance: {text}"
    );
    assert!(
        text.contains("too large for a per-item error listing"),
        "the violation must explain why the listing was skipped: {text}"
    );

    // Same size, schema holds: `contract ok`, not a violation.
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: big.json\n    format: json\n    schema:\n      type: array\n      items: {type: number}\n",
    )
    .unwrap();
    let output = ToolKind::Validate
        .execute(&serde_json::json!({}), &ctx(&job, &[skill]))
        .await;
    assert!(!output.is_error, "a passing large file stays a pass");
    assert_eq!(result_text(&output), "contract ok (1 files checked)");
}

#[test]
fn validate_tool_when_not_enabled_gets_a_tool_error() {
    // The agent loop rejects tool calls for tools absent from --tools; the
    // model sees a normal tool error naming the enabled set. Driven through
    // the real binary + stub fixture (mirrors tests/agent_loop.rs).
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    std::fs::write(cwd.join("prompt.md"), "Validate nothing.").unwrap();
    std::fs::write(
        cwd.join("fixture.json"),
        r#"{"responses": [
  {"content": [{"type": "toolCall", "name": "validate", "arguments": {}}]},
  {"content": [{"type": "text", "text": "ok"}], "stopReason": "stop"}
]}"#,
    )
    .unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_velites"))
        .args([
            "--provider",
            "stub",
            "--stub-fixture",
            "fixture.json",
            "--tools",
            "read",
            "--no-sandbox",
            "@prompt.md",
        ])
        .current_dir(cwd)
        .output()
        .expect("failed to spawn velites");
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let tool_end = stdout
        .lines()
        .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
        .find(|event| event["type"] == "tool_execution_end")
        .expect("tool_execution_end missing");
    assert_eq!(tool_end["isError"], true);
    let text = tool_end["result"]["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("`validate` is not enabled"), "{text}");
    assert!(text.contains("enabled: read"), "{text}");
}
