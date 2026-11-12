//! #865 truncation continuation, end-to-end through the real `velites`
//! binary driven by the stub provider: a length-truncated reply
//! (`stopReason=length`) no longer ends the run — a continuation turn is
//! injected (budgets unchanged), and only CONSECUTIVE truncations reaching
//! the cap fall back to the pre-#865 remediation ending. History
//! sanitization itself is pinned by tests/agent_loop.rs (ScriptedProvider
//! records the model-facing requests); these tests pin the observable
//! process behavior: exit codes, event sequence, stderr trace.

use std::path::Path;
use std::process::Command;

fn write(path: &Path, content: &str) {
    std::fs::write(path, content).expect("failed to write test file");
}

fn run_velites(cwd: &Path, fixture: &Path, extra_args: &[&str]) -> std::process::Output {
    let mut args: Vec<String> = vec![
        "--mode".into(),
        "json".into(),
        "--name".into(),
        "truncation-865".into(),
        "--provider".into(),
        "stub".into(),
        "--stub-fixture".into(),
        fixture.to_string_lossy().into_owned(),
        "--tools".into(),
        "write".into(),
        "--system-prompt".into(),
        "You are a test agent.".into(),
        "--require-output".into(),
        "result.json".into(),
        // The continuation logic is orthogonal to confinement; CI's Linux
        // lane has no bwrap, and the default-on sandbox fails closed.
        "--no-sandbox".into(),
        "Write result.json.".into(),
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

fn assistant_stop_reasons(events: &[serde_json::Value]) -> Vec<String> {
    events
        .iter()
        .filter(|event| event["type"] == "message_end" && event["message"]["role"] == "assistant")
        .map(|event| {
            event["message"]["stopReason"]
                .as_str()
                .unwrap_or("")
                .to_string()
        })
        .collect()
}

#[test]
fn truncation_continuation_writes_outputs_and_completes() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [
        {"type": "text", "text": "partial"},
        {"type": "toolCall", "name": "write", "arguments": {"path": "result.json", "content": "{\"never\": true}"}}
      ],
      "stopReason": "length",
      "usage": {"input": 100, "output": 9000, "cacheRead": 0}
    },
    {
      "content": [
        {"type": "toolCall", "name": "write", "arguments": {"path": "result.json", "content": "{\"ok\": true}"}}
      ],
      "usage": {"input": 120, "output": 30, "cacheRead": 0}
    },
    {
      "content": [{"type": "text", "text": "done"}],
      "stopReason": "stop",
      "usage": {"input": 140, "output": 8, "cacheRead": 0}
    }
  ]
}"#,
    );

    let output = run_velites(cwd, &cwd.join("fixture.json"), &[]);
    assert!(
        output.status.success(),
        "velites exited non-zero: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    // The truncated first turn's tool call never ran; the continuation
    // turn's write produced the declared artifact.
    assert_eq!(
        std::fs::read_to_string(cwd.join("result.json")).unwrap(),
        "{\"ok\": true}"
    );

    let events = parse_events(&output.stdout);
    assert_eq!(
        assistant_stop_reasons(&events),
        vec!["length", "toolUse", "stop"]
    );
    // One stderr trace per truncation (turn + output tokens).
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("per-call output token limit (turn 1, 9000 output tokens"),
        "stderr missing the truncation trace: {stderr}"
    );
    // Successful ending: validation reports nothing missing, no end reason.
    let validation = events
        .iter()
        .find(|event| event["type"] == "outputs_validation")
        .expect("outputs_validation must be emitted");
    assert_eq!(validation["missing"], serde_json::json!([]));
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert!(agent_end.get("reason").is_none());
}

#[test]
fn consecutive_truncations_fall_back_to_the_remediation_ending() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [{"type": "text", "text": "cut off"}],
      "stopReason": "length",
      "usage": {"input": 100, "output": 9000, "cacheRead": 0}
    },
    {
      "content": [{"type": "text", "text": "cut off again"}],
      "stopReason": "length",
      "usage": {"input": 120, "output": 9000, "cacheRead": 0}
    },
    {
      "content": [{"type": "text", "text": "cannot finish"}],
      "stopReason": "stop",
      "usage": {"input": 140, "output": 12, "cacheRead": 0}
    }
  ]
}"#,
    );

    let output = run_velites(cwd, &cwd.join("fixture.json"), &[]);
    // Continuation (turn 2) truncated again → cap reached → ONE remediation
    // turn (turn 3) that writes nothing → output-contract exit.
    assert_eq!(output.status.code(), Some(1));
    assert!(!cwd.join("result.json").exists());

    let events = parse_events(&output.stdout);
    assert_eq!(
        assistant_stop_reasons(&events),
        vec!["length", "length", "stop"]
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("per-call output token limit (turn 2, 9000 output tokens"),
        "stderr missing the second truncation trace: {stderr}"
    );
    let validation = events
        .iter()
        .find(|event| event["type"] == "outputs_validation")
        .expect("outputs_validation must be emitted");
    assert_eq!(validation["missing"], serde_json::json!(["result.json"]));
    // No delta events may ever appear.
    let raw = String::from_utf8_lossy(&output.stdout);
    assert!(!raw.contains("message_update"));
    assert!(!raw.contains("tool_execution_update"));
}
