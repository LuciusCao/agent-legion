//! Controllability integration tests (design §5): token-budget wrap-up,
//! `--require-output` remediation + `outputs_validation`, and sandbox
//! validation of declared outputs. SIGTERM cancellation is covered by the
//! Python integration tests (`tests/executors/test_velites_controllability.py`).

use std::path::Path;
use std::process::Command;

fn write(path: &Path, content: &str) {
    std::fs::write(path, content).expect("failed to write test file");
}

fn run_velites(cwd: &Path, fixture: &Path, extra_args: &[&str]) -> std::process::Output {
    let mut args: Vec<String> = vec![
        "--mode".into(),
        "json".into(),
        "--provider".into(),
        "stub".into(),
        "--stub-fixture".into(),
        fixture.to_string_lossy().into_owned(),
        // These tests exercise controllability, not confinement; CI's Linux
        // lane has no bwrap, and the default-on sandbox fails closed.
        // Confinement itself is covered by tests/os_sandbox.rs.
        "--no-sandbox".into(),
        "@prompt.md".into(),
    ];
    args.extend(extra_args.iter().map(|s| s.to_string()));
    Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(&args)
        .current_dir(cwd)
        .output()
        .expect("failed to spawn velites")
}

fn parse_events(stdout: &[u8]) -> Vec<serde_json::Value> {
    String::from_utf8_lossy(stdout)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("each stdout line must be valid JSON"))
        .collect()
}

#[test]
fn max_tokens_triggers_budget_exceeded() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Work.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}],
      "usage": {"input": 40, "output": 20, "cacheRead": 0}
    },
    {"content": [{"type": "text", "text": "wrapping up"}]}
  ]
}"#,
    );

    // After turn 1 the cumulative usage (60) exceeds the budget; the model
    // gets one wrap-up turn, then the run ends with the budget reason.
    let output = run_velites(cwd, &cwd.join("fixture.json"), &["--max-tokens", "50"]);
    assert!(output.status.success());
    let events = parse_events(&output.stdout);
    assert_eq!(
        events.iter().filter(|e| e["type"] == "turn_start").count(),
        2
    );
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert_eq!(agent_end["reason"], "budget_exceeded");
    let history = agent_end["messages"].as_array().unwrap();
    assert!(history.iter().any(|m| m["role"] == "user"
        && m["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("--max-tokens")));
}

#[test]
fn require_output_remediation_then_validation_ok() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Produce result.txt.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "text", "text": "done without writing"}]},
    {"content": [{"type": "toolCall", "name": "write", "arguments": {"path": "result.txt", "content": "payload"}}]},
    {"content": [{"type": "text", "text": "written"}]}
  ]
}"#,
    );

    let output = run_velites(
        cwd,
        &cwd.join("fixture.json"),
        &["--require-output", "result.txt"],
    );
    assert!(output.status.success());
    assert!(cwd.join("result.txt").exists());

    let events = parse_events(&output.stdout);
    let validations: Vec<&serde_json::Value> = events
        .iter()
        .filter(|e| e["type"] == "outputs_validation")
        .collect();
    assert_eq!(validations.len(), 1, "exactly one outputs_validation event");
    assert_eq!(validations[0]["missing"], serde_json::json!([]));
    // The validation event precedes agent_end.
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert!(agent_end.get("reason").is_none());
    // The remediation notice names the missing artifact.
    let history = agent_end["messages"].as_array().unwrap();
    assert!(history.iter().any(|m| m["role"] == "user"
        && m["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("result.txt")));
}

#[test]
fn require_output_still_missing_reports_validation() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Produce result.txt.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "text", "text": "nothing"}]},
    {"content": [{"type": "text", "text": "still nothing"}]}
  ]
}"#,
    );

    let output = run_velites(
        cwd,
        &cwd.join("fixture.json"),
        &["--require-output", "result.txt"],
    );
    assert!(output.status.success());
    let events = parse_events(&output.stdout);
    let validation = events
        .iter()
        .find(|e| e["type"] == "outputs_validation")
        .expect("outputs_validation event missing");
    assert_eq!(validation["missing"], serde_json::json!(["result.txt"]));
    // Exactly one remediation turn: 2 turns total.
    assert_eq!(
        events.iter().filter(|e| e["type"] == "turn_start").count(),
        2
    );
    assert!(events.last().unwrap().get("reason").is_none());
}

#[test]
fn require_output_escape_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Hi.");
    write(
        &cwd.join("fixture.json"),
        r#"{"responses": [{"content": [{"type": "text", "text": "ok"}]}]}"#,
    );

    let output = run_velites(
        cwd,
        &cwd.join("fixture.json"),
        &["--require-output", "../escape.txt"],
    );
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("--require-output"));
}
