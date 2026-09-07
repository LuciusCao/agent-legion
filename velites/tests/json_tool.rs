//! `json` tool integration tests (#518): the read-modify-write primitive
//! driven end-to-end through the real binary with the stub provider —
//! the scenario the issue describes (patch one field of a large JSON
//! artifact, no whole-file rewrite, no bash heredoc python).

use std::path::Path;
use std::process::Command;

fn write(path: &Path, content: &str) {
    std::fs::write(path, content).expect("failed to write test file");
}

/// The artifact shape from the issue: a spec with entries the model needs
/// to patch field-by-field.
fn write_spec(cwd: &Path) {
    write(
        &cwd.join("key_info_spec.json"),
        r#"{"entries": [{"id": "k1", "content": "needs fixing"}, {"id": "k2", "content": "fine"}], "meta": {"version": 1}}"#,
    );
}

fn fixture_with_json_ops(path: &Path) {
    write(
        path,
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "json", "arguments": {"op": "get", "path": "key_info_spec.json", "query": "entries[0].content"}}]},
    {"content": [{"type": "toolCall", "name": "json", "arguments": {"op": "set", "path": "key_info_spec.json", "query": "entries[0].content", "value": "corrected content"}}]},
    {"content": [{"type": "toolCall", "name": "json", "arguments": {"op": "delete", "path": "key_info_spec.json", "query": "entries[1]"}}]},
    {"content": [{"type": "text", "text": "patched"}], "stopReason": "stop"}
  ]
}"#,
    );
}

fn run_velites(cwd: &Path, tools: &str) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_velites"))
        .args([
            "--provider",
            "stub",
            "--stub-fixture",
            "fixture.json",
            "--tools",
            tools,
            // Same rationale as golden_events: the contract is under test,
            // not confinement; CI lanes without a sandbox backend fail
            // closed by default.
            "--no-sandbox",
            "@prompt.md",
            "Execute the attached node instructions.",
        ])
        .current_dir(cwd)
        .output()
        .expect("failed to spawn velites")
}

fn tool_events(stdout: &[u8]) -> Vec<serde_json::Value> {
    let events: Vec<serde_json::Value> = String::from_utf8_lossy(stdout)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("valid NDJSON"))
        .collect();
    events
        .into_iter()
        .filter(|event| event["type"] == "tool_execution_end")
        .collect()
}

#[test]
fn json_tool_round_trip_get_set_delete() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write_spec(cwd);
    write(&cwd.join("prompt.md"), "Patch the spec.");
    fixture_with_json_ops(&cwd.join("fixture.json"));

    let output = run_velites(cwd, "read,write,bash,json");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    let events = tool_events(&output.stdout);
    assert_eq!(events.len(), 3, "three json tool rounds must run");

    // get: the queried value comes back as content.
    assert_eq!(events[0]["toolName"], "json");
    assert_eq!(events[0]["isError"], false);
    assert_eq!(
        events[0]["result"]["content"][0]["text"],
        "\"needs fixing\""
    );

    // set + delete: confirmations, and the file on disk reflects them.
    assert_eq!(events[1]["isError"], false);
    assert_eq!(events[2]["isError"], false);

    let patched: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(cwd.join("key_info_spec.json")).unwrap())
            .unwrap();
    assert_eq!(patched["entries"][0]["content"], "corrected content");
    assert_eq!(patched["entries"].as_array().unwrap().len(), 1);
    assert_eq!(patched["meta"]["version"], 1);
}

#[test]
fn json_tool_not_enabled_reports_not_enabled() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write_spec(cwd);
    write(&cwd.join("prompt.md"), "Patch the spec.");
    fixture_with_json_ops(&cwd.join("fixture.json"));

    // json NOT in --tools: every model call still produces a tool round,
    // but each lands on the not-enabled error path.
    let output = run_velites(cwd, "read,write,bash");
    let events = tool_events(&output.stdout);
    assert_eq!(events.len(), 3);
    for event in &events {
        assert_eq!(event["isError"], true);
        let text = event["result"]["content"][0]["text"].as_str().unwrap();
        assert!(
            text.contains("`json` is not enabled"),
            "expected the not-enabled error, got: {text}"
        );
    }
    // The file is untouched.
    let spec: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(cwd.join("key_info_spec.json")).unwrap())
            .unwrap();
    assert_eq!(spec["entries"][0]["content"], "needs fixing");
}
