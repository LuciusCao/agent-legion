//! Validate tool tests (#443): the three tool states (contract ok /
//! violations / no contract block) and the agent-loop "not enabled" path
//! when `validate` is absent from `--tools`.

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

/// Skill dir with a one-file text contract (min_chars 10, heading `## 目标`).
fn contract_skill(dir: &Path) -> PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "# Contract\n\n```yaml contract\nfiles:\n  - path: script.md\n    format: text\n    min_chars: 10\n    required_headings: [\"## 目标\"]\n```\n",
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
async fn validate_without_contract_block_is_an_informational_error() {
    let dir = tempfile::tempdir().unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();

    // No --skill dirs at all, and a skill dir without a contract block both
    // land on the same "nothing to validate against" error.
    for dirs in [Vec::new(), vec![skill]] {
        let output = ToolKind::Validate
            .execute(&serde_json::json!({}), &ctx(&job, &dirs))
            .await;
        assert!(output.is_error);
        assert_eq!(
            result_text(&output),
            "no output-contract.md contract block found in the skill directories; \
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
