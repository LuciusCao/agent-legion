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
        "--session-dir".into(),
        cwd.join("session").to_string_lossy().into_owned(),
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

/// Session mirror messages (schema v2: `agent_end` no longer carries the
/// history, so injected user notices are verified via the session log).
fn session_messages(cwd: &Path) -> Vec<serde_json::Value> {
    std::fs::read_to_string(cwd.join("session/session.jsonl"))
        .expect("session log missing")
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
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
    assert!(agent_end.get("messages").is_none());
    let history = session_messages(cwd);
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
    // No --skill dir with a contract block: legacy existence mode (#443).
    assert_eq!(validations[0]["mode"], "existence");
    assert_eq!(validations[0]["violations"], serde_json::json!([]));
    // The validation event precedes agent_end.
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert!(agent_end.get("reason").is_none());
    // The remediation notice names the missing artifact.
    let history = session_messages(cwd);
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
    // Output contract violation: still missing after the remediation turn.
    assert_eq!(output.status.code(), Some(1));
    let events = parse_events(&output.stdout);
    let validation = events
        .iter()
        .find(|e| e["type"] == "outputs_validation")
        .expect("outputs_validation event missing");
    assert_eq!(validation["missing"], serde_json::json!(["result.txt"]));
    assert_eq!(validation["mode"], "existence");
    // Exactly one remediation turn: 2 turns total.
    assert_eq!(
        events.iter().filter(|e| e["type"] == "turn_start").count(),
        2
    );
    assert!(events.last().unwrap().get("reason").is_none());
}

#[test]
fn model_error_with_missing_outputs_exits_nonzero() {
    // The false-completion incident class: the model call fails
    // (stopReason=error), the declared artifact is never produced, and the
    // run must NOT exit 0 — callers treat exit 0 as success.
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Produce result.txt.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [],
      "stopReason": "error",
      "errorMessage": "upstream 401: unauthorized",
      "usage": {"input": 0, "output": 0, "cacheRead": 0}
    }
  ]
}"#,
    );

    let output = run_velites(
        cwd,
        &cwd.join("fixture.json"),
        &["--require-output", "result.txt"],
    );
    assert_eq!(output.status.code(), Some(1));
    let events = parse_events(&output.stdout);
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert_eq!(agent_end["error"], "upstream 401: unauthorized");
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

#[test]
fn read_tool_reads_absolute_path_inside_skill_dir() {
    // End-to-end wiring (design §5): a --skill directory OUTSIDE the job dir
    // is a read-only root, so the read tool accepts absolute paths into it.
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().join("job");
    let skill = dir.path().join("skill");
    std::fs::create_dir(&cwd).unwrap();
    std::fs::create_dir_all(skill.join("references")).unwrap();
    write(&cwd.join("prompt.md"), "Read the skill reference.");
    write(&skill.join("SKILL.md"), "# Demo skill\n");
    write(&skill.join("references/data.json"), "{\"k\": 1}");
    let reference = skill.join("references/data.json");
    write(
        &cwd.join("fixture.json"),
        &format!(
            r#"{{"responses": [
  {{"content": [{{"type": "toolCall", "name": "read", "arguments": {{"path": "{}"}}}}]}},
  {{"content": [{{"type": "text", "text": "done"}}], "stopReason": "stop"}}
]}}"#,
            reference.display()
        ),
    );

    let output = run_velites(
        &cwd,
        &cwd.join("fixture.json"),
        &["--skill", &skill.to_string_lossy()],
    );
    assert!(
        output.status.success(),
        "velites exited non-zero: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = parse_events(&output.stdout);
    let tool_end = events
        .iter()
        .find(|e| e["type"] == "tool_execution_end")
        .expect("tool_execution_end missing");
    assert_eq!(
        tool_end["isError"], false,
        "read inside --skill dir must succeed: {tool_end}"
    );
    assert_eq!(tool_end["result"]["content"][0]["text"], "{\"k\": 1}");
}

// #443: the --require-output gate upgrades to contract mode when a --skill
// directory declares a `yaml contract` block in references/output-contract.md.

/// Skill dir (OUTSIDE the job dir, like production skill roots) whose
/// contract demands a non-trivial `script.md` text artifact.
fn contract_skill(dir: &Path) -> std::path::PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    write(&skill.join("SKILL.md"), "# Demo skill\n");
    write(
        &skill.join("references/output-contract.md"),
        "# Contract\n\n```yaml contract\nfiles:\n  - path: script.md\n    format: text\n    min_chars: 10\n    required_headings: [\"## 目标\"]\n```\n",
    );
    skill
}

fn contract_gate_events(stdout: &[u8]) -> (Vec<serde_json::Value>, Vec<serde_json::Value>) {
    let events = parse_events(stdout);
    let validations: Vec<serde_json::Value> = events
        .iter()
        .filter(|e| e["type"] == "outputs_validation")
        .cloned()
        .collect();
    (events, validations)
}

#[test]
fn contract_mode_all_rules_pass() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().join("job");
    std::fs::create_dir(&cwd).unwrap();
    let skill = contract_skill(dir.path());
    write(&cwd.join("prompt.md"), "Produce script.md.");
    write(
        &cwd.join("fixture.json"),
        r###"{"responses": [
  {"content": [{"type": "toolCall", "name": "write", "arguments": {"path": "script.md", "content": "## 目标\nlong enough content"}}]},
  {"content": [{"type": "text", "text": "written"}]}
]}"###,
    );

    let output = run_velites(
        &cwd,
        &cwd.join("fixture.json"),
        &[
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "script.md",
        ],
    );
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let (_events, validations) = contract_gate_events(&output.stdout);
    assert_eq!(validations.len(), 1);
    assert_eq!(validations[0]["mode"], "contract");
    assert_eq!(validations[0]["missing"], serde_json::json!([]));
    assert_eq!(validations[0]["violations"], serde_json::json!([]));
}

#[test]
fn contract_violation_triggers_remediation_then_passes() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().join("job");
    std::fs::create_dir(&cwd).unwrap();
    let skill = contract_skill(dir.path());
    write(&cwd.join("prompt.md"), "Produce script.md.");
    // Turn 1 stops without the artifact (missing + contract violation);
    // the remediation turn writes a compliant file with the write tool.
    write(
        &cwd.join("fixture.json"),
        r###"{"responses": [
  {"content": [{"type": "text", "text": "done without writing"}]},
  {"content": [{"type": "toolCall", "name": "write", "arguments": {"path": "script.md", "content": "## 目标\nlong enough content"}}]}
]}"###,
    );

    let output = run_velites(
        &cwd,
        &cwd.join("fixture.json"),
        &[
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "script.md",
        ],
    );
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let (events, validations) = contract_gate_events(&output.stdout);
    assert_eq!(validations.len(), 1);
    assert_eq!(validations[0]["mode"], "contract");
    assert_eq!(validations[0]["violations"], serde_json::json!([]));
    // Exactly one remediation turn ran and the notice names the violation.
    assert_eq!(
        events.iter().filter(|e| e["type"] == "turn_start").count(),
        2
    );
    let history = session_messages(&cwd);
    assert!(history.iter().any(|m| m["role"] == "user"
        && m["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("script.md: missing required file")));
}

#[test]
fn contract_violation_unfixed_exits_nonzero() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().join("job");
    std::fs::create_dir(&cwd).unwrap();
    let skill = contract_skill(dir.path());
    write(&cwd.join("prompt.md"), "Produce script.md.");
    write(
        &cwd.join("fixture.json"),
        r#"{"responses": [
  {"content": [{"type": "text", "text": "nothing"}]},
  {"content": [{"type": "text", "text": "still nothing"}]}
]}"#,
    );

    let output = run_velites(
        &cwd,
        &cwd.join("fixture.json"),
        &[
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "script.md",
        ],
    );
    // Contract mode is fail-closed like the existence gate: exit 1.
    assert_eq!(output.status.code(), Some(1));
    let (_events, validations) = contract_gate_events(&output.stdout);
    assert_eq!(validations.len(), 1);
    assert_eq!(validations[0]["mode"], "contract");
    assert_eq!(validations[0]["missing"], serde_json::json!(["script.md"]));
    assert_eq!(
        validations[0]["violations"],
        serde_json::json!(["script.md: missing required file"])
    );
}

#[test]
fn contract_parse_error_fails_the_gate_closed() {
    // A malformed contract block is a violation (mode=contract), never a
    // silent downgrade to existence mode.
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path().join("job");
    std::fs::create_dir(&cwd).unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    write(&skill.join("SKILL.md"), "# Demo skill\n");
    write(
        &skill.join("references/output-contract.md"),
        "```yaml contract\nfiles: []\n```\n",
    );
    write(&cwd.join("prompt.md"), "Hi.");
    write(
        &cwd.join("fixture.json"),
        r#"{"responses": [
  {"content": [{"type": "text", "text": "nothing"}]},
  {"content": [{"type": "text", "text": "still nothing"}]}
]}"#,
    );

    let output = run_velites(
        &cwd,
        &cwd.join("fixture.json"),
        &[
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "script.md",
        ],
    );
    assert_eq!(output.status.code(), Some(1));
    let (_events, validations) = contract_gate_events(&output.stdout);
    assert_eq!(validations[0]["mode"], "contract");
    let violation = validations[0]["violations"][0].as_str().unwrap();
    assert!(
        violation.starts_with("contract parse error:"),
        "{violation}"
    );
    // A parse error lives in the read-only skill dir — the model can never
    // fix it, so there must be NO remediation turn (a single turn total).
    assert_eq!(
        _events.iter().filter(|e| e["type"] == "turn_start").count(),
        1,
        "parse-error gate must skip the futile remediation turn"
    );
}
