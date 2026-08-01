//! Golden event-sequence test: drive the real `velites` binary with the stub
//! provider through a full session (one toolUse round + final stop) and assert
//! the stdout NDJSON event sequence and key field values against the design
//! doc §4 contract.

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
        "golden-1".into(),
        "--provider".into(),
        "stub".into(),
        "--stub-fixture".into(),
        fixture.to_string_lossy().into_owned(),
        "--session-dir".into(),
        cwd.join("session").to_string_lossy().into_owned(),
        "--tools".into(),
        "read,write,bash".into(),
        "--system-prompt".into(),
        "You are a test agent.".into(),
        // Golden events assert the wire sequence, not confinement; CI's
        // Linux lane has no bwrap, and the default-on sandbox fails closed.
        "--no-sandbox".into(),
        "@prompt.md".into(),
        "Execute the attached node instructions.".into(),
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
fn golden_event_sequence_with_tool_round() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(
        &cwd.join("prompt.md"),
        "Read input.txt and report its content.",
    );
    write(&cwd.join("input.txt"), "hello velites");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [
        {"type": "thinking", "thinking": "I should read the file."},
        {"type": "text", "text": "Reading input.txt."},
        {"type": "toolCall", "name": "read", "arguments": {"path": "input.txt"}}
      ],
      "usage": {"input": 10, "output": 5, "cacheRead": 3}
    },
    {
      "content": [{"type": "text", "text": "The file says: hello velites"}],
      "stopReason": "stop",
      "usage": {"input": 25, "output": 8, "cacheRead": 0}
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

    let events = parse_events(&output.stdout);
    let types: Vec<&str> = events.iter().map(|e| e["type"].as_str().unwrap()).collect();
    assert_eq!(
        types,
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "turn_end",
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
        ]
    );

    // No delta events may ever appear.
    let raw = String::from_utf8_lossy(&output.stdout);
    assert!(!raw.contains("message_update"));
    assert!(!raw.contains("tool_execution_update"));

    // session: sessionId from --name.
    assert_eq!(events[0]["sessionId"], "golden-1");

    // First message_end: assistant with thinking + text + toolCall.
    let msg1 = &events[4]["message"];
    assert_eq!(msg1["role"], "assistant");
    assert_eq!(msg1["provider"], "stub");
    assert_eq!(msg1["model"], "stub");
    assert_eq!(msg1["stopReason"], "toolUse");
    assert_eq!(msg1["usage"]["input"], 10);
    assert_eq!(msg1["usage"]["output"], 5);
    assert_eq!(msg1["usage"]["cacheRead"], 3);
    assert!(msg1.get("errorMessage").is_none());
    assert_eq!(msg1["content"][0]["type"], "thinking");
    assert_eq!(msg1["content"][0]["thinking"], "I should read the file.");
    assert_eq!(msg1["content"][1]["type"], "text");
    let tool_call = &msg1["content"][2];
    assert_eq!(tool_call["type"], "toolCall");
    assert_eq!(tool_call["name"], "read");
    let tool_call_id = tool_call["id"].as_str().unwrap();
    assert!(!tool_call_id.is_empty());

    // Tool execution: matching ids, pi-compatible result, output_bytes measured.
    assert_eq!(events[5]["toolCallId"], tool_call_id);
    assert_eq!(events[5]["toolName"], "read");
    assert_eq!(events[5]["args"]["path"], "input.txt");
    let tool_end = &events[6];
    assert_eq!(tool_end["toolCallId"], tool_call_id);
    assert_eq!(tool_end["toolName"], "read");
    assert_eq!(tool_end["isError"], false);
    assert_eq!(tool_end["result"]["content"][0]["type"], "text");
    assert_eq!(tool_end["result"]["content"][0]["text"], "hello velites");
    assert!(tool_end["output_bytes"].as_u64().unwrap() > 0);

    // turn_end carries the assistant message + toolResults (toolResult role).
    let turn_end = &events[7];
    assert_eq!(turn_end["turnIndex"], 1);
    assert_eq!(turn_end["message"]["stopReason"], "toolUse");
    let tool_results = turn_end["toolResults"].as_array().unwrap();
    assert_eq!(tool_results.len(), 1);
    assert_eq!(tool_results[0]["role"], "toolResult");
    assert_eq!(tool_results[0]["toolCallId"], tool_call_id);
    assert_eq!(tool_results[0]["toolName"], "read");
    assert_eq!(tool_results[0]["isError"], false);

    // Second message_end: final stop.
    let msg2 = &events[10]["message"];
    assert_eq!(msg2["stopReason"], "stop");
    assert_eq!(msg2["usage"]["input"], 25);
    assert_eq!(msg2["usage"]["output"], 8);
    assert_eq!(msg2["usage"]["cacheRead"], 0);
    assert_eq!(msg2["content"][0]["text"], "The file says: hello velites");

    // agent_end: full history, no error.
    let agent_end = &events[12];
    assert!(agent_end.get("error").is_none());
    let history = agent_end["messages"].as_array().unwrap();
    assert_eq!(history.len(), 4); // user + assistant + toolResult + assistant
    assert_eq!(history[0]["role"], "user");
    assert!(history[0]["content"][0]["text"]
        .as_str()
        .unwrap()
        .contains("Read input.txt and report its content."));
    assert!(history[0]["content"][0]["text"]
        .as_str()
        .unwrap()
        .contains("Execute the attached node instructions."));

    // Session mirror: one line per message, append-only NDJSON.
    let session_log = std::fs::read_to_string(cwd.join("session/session.jsonl")).unwrap();
    let session_roles: Vec<String> = session_log
        .lines()
        .map(|line| {
            let v: serde_json::Value = serde_json::from_str(line).unwrap();
            v["role"].as_str().unwrap().to_string()
        })
        .collect();
    assert_eq!(
        session_roles,
        vec!["user", "assistant", "toolResult", "assistant"]
    );
}

#[test]
fn model_error_still_exits_zero() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Do something.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {
      "content": [],
      "stopReason": "error",
      "errorMessage": "upstream 400: bad request",
      "usage": {"input": 0, "output": 0, "cacheRead": 0}
    }
  ]
}"#,
    );

    let output = run_velites(cwd, &cwd.join("fixture.json"), &[]);
    assert!(
        output.status.success(),
        "model error must keep exit 0 (Host judges failure from events)"
    );
    let events = parse_events(&output.stdout);
    let types: Vec<&str> = events.iter().map(|e| e["type"].as_str().unwrap()).collect();
    assert_eq!(
        types,
        vec![
            "session",
            "agent_start",
            "turn_start",
            "message_start",
            "message_end",
            "turn_end",
            "agent_end",
        ]
    );
    assert_eq!(events[4]["message"]["stopReason"], "error");
    assert_eq!(
        events[4]["message"]["errorMessage"],
        "upstream 400: bad request"
    );
    assert_eq!(events[6]["error"], "upstream 400: bad request");
}

#[test]
fn unknown_flags_are_rejected() {
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["--mode", "json", "--definitely-not-a-flag", "hi"])
        .output()
        .expect("failed to spawn velites");
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("--definitely-not-a-flag"));
}

#[test]
fn max_turns_triggers_wrap_up_and_budget_exceeded() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write(&cwd.join("prompt.md"), "Loop forever.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
    {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
    {"content": [{"type": "text", "text": "wrapping up"}]}
  ]
}"#,
    );

    let output = run_velites(cwd, &cwd.join("fixture.json"), &["--max-turns", "2"]);
    assert!(output.status.success());
    let events = parse_events(&output.stdout);
    // Two budgeted turns + one wrap-up turn.
    let turn_starts = events.iter().filter(|e| e["type"] == "turn_start").count();
    assert_eq!(turn_starts, 3);
    let agent_end = events.last().unwrap();
    assert_eq!(agent_end["type"], "agent_end");
    assert_eq!(agent_end["reason"], "budget_exceeded");
    assert!(agent_end.get("error").is_none());
    // The wrap-up notice was injected as a user message.
    let history = agent_end["messages"].as_array().unwrap();
    let notice = history
        .iter()
        .find(|m| {
            m["role"] == "user"
                && m["content"][0]["text"]
                    .as_str()
                    .unwrap()
                    .contains("FINAL turn")
        })
        .expect("budget wrap-up notice missing from history");
    assert!(notice["content"][0]["text"]
        .as_str()
        .unwrap()
        .contains("--max-turns"));
}
