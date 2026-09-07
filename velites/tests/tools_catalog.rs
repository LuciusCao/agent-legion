//! `velites tools list --json` (#476): the machine-readable tool catalog
//! consumed by the Host runtime adapter (cross-binary parity is pinned on
//! the Python side in `tests/executors/test_velites_event_contract.py`
//! style) plus the `--require-output` → validate forced-activation states.

use std::path::Path;
use std::process::Command;

fn write(path: &Path, content: &str) {
    std::fs::write(path, content).expect("failed to write test file");
}

fn run_velites(cwd: &Path, extra_args: &[&str]) -> std::process::Output {
    let mut args: Vec<String> = vec![
        "--provider".into(),
        "stub".into(),
        // The catalog contract, not confinement, is under test; the
        // default-on sandbox fails closed where CI has no backend.
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
fn tools_list_json_emits_the_catalog() {
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["tools", "list", "--json"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(value["version"].is_string());
    let tools = value["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["read", "write", "bash", "uuid", "json", "validate"]);
    for tool in tools {
        assert!(tool["description"].is_string());
        assert!(tool["parameters"].is_object());
    }
}

#[test]
fn tools_list_without_json_fails() {
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["tools", "list"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("requires --json"));
}

#[test]
fn tools_list_requires_the_list_token() {
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["tools", "describe"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("expected `velites tools list --json`")
    );
}

/// Skill dir with a parseable one-file text contract.
fn contract_skill(dir: &Path) -> std::path::PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    write(&skill.join("SKILL.md"), "# Skill\n\nDeclares a contract.\n");
    write(
        &skill.join("references/output-contract.md"),
        "# Contract\n\n```yaml contract\nfiles:\n  - path: out.md\n    format: text\n    min_chars: 1\n```\n",
    );
    skill
}

/// A one-turn stub fixture: the model calls `validate`, then stops.
fn validate_then_stop_fixture(path: &Path) {
    write(
        path,
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "validate", "arguments": {}}]},
    {"content": [{"type": "text", "text": "done"}], "stopReason": "stop"}
  ]
}"#,
    );
}

/// --require-output + parseable contract → validate is advertised even
/// though `--tools` omits it: the model's validate call executes as a real
/// tool round (no "not enabled" error content).
#[test]
fn require_output_with_contract_advertises_validate() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    let skill = contract_skill(cwd);
    write(&cwd.join("prompt.md"), "Self-check then stop.");
    validate_then_stop_fixture(&cwd.join("fixture.json"));

    let output = run_velites(
        cwd,
        &[
            "--stub-fixture",
            "fixture.json",
            "--tools",
            "read,write,bash",
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "out.md",
        ],
    );
    // out.md is still missing at the end, so the exit-contract gate fires
    // (exit 1 + a remediation turn that exhausts the fixture) — expected;
    // the assertion under test is the ADVERTISED tool, not the exit code.
    let events = parse_events(&output.stdout);
    let tool_end = events
        .iter()
        .find(|e| e["type"] == "tool_execution_end")
        .expect("validate tool round must run");
    assert_eq!(tool_end["toolName"], "validate");
    // Violations come back as is_error content (the file does not exist yet);
    // the point here is that the call EXECUTED, not that it passed.
    assert_eq!(tool_end["isError"], true);
    let text = tool_end["result"]["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("contract violations"),
        "expected a real contract check, got: {text}"
    );
}

/// --require-output with NO contract block → validate stays unadvertised:
/// the model's validate call returns the "not enabled" error content.
#[test]
fn require_output_without_contract_keeps_validate_unadvertised() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    // Skill exists (SKILL.md) but declares no output contract.
    let skill = cwd.join("skill");
    std::fs::create_dir_all(&skill).unwrap();
    write(&skill.join("SKILL.md"), "# Skill\n\nNo contract.\n");
    write(&cwd.join("prompt.md"), "Self-check then stop.");
    validate_then_stop_fixture(&cwd.join("fixture.json"));

    let output = run_velites(
        cwd,
        &[
            "--stub-fixture",
            "fixture.json",
            "--tools",
            "read,write,bash",
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "out.md",
        ],
    );
    // The run itself still exits 1: out.md is missing at the end (exit
    // contract), but the process must not crash — assert on the stream.
    let events = parse_events(&output.stdout);
    let tool_end = events
        .iter()
        .find(|e| e["type"] == "tool_execution_end")
        .expect("the model's validate call must produce a tool round");
    assert_eq!(tool_end["toolName"], "validate");
    let text = tool_end["result"]["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("is not enabled"),
        "expected the not-enabled error, got: {text}"
    );
}

/// A malformed contract block must NOT activate validate: the model cannot
/// fix a syntax error in the read-only skill directory.
#[test]
fn require_output_with_broken_contract_keeps_validate_unadvertised() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    let skill = cwd.join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    write(&skill.join("SKILL.md"), "# Skill\nBroken contract.\n");
    write(
        &skill.join("references/output-contract.md"),
        "# Broken\n\n```yaml contract\nfiles: [not a mapping\n```\n",
    );
    write(&cwd.join("prompt.md"), "Self-check then stop.");
    validate_then_stop_fixture(&cwd.join("fixture.json"));

    let output = run_velites(
        cwd,
        &[
            "--stub-fixture",
            "fixture.json",
            "--tools",
            "read,write,bash",
            "--skill",
            &skill.to_string_lossy(),
            "--require-output",
            "out.md",
        ],
    );
    let events = parse_events(&output.stdout);
    let tool_end = events
        .iter()
        .find(|e| e["type"] == "tool_execution_end")
        .expect("the model's validate call must produce a tool round");
    let text = tool_end["result"]["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("is not enabled"),
        "expected the not-enabled error, got: {text}"
    );
}

/// `--tools validate` without `--require-output` is accepted as a no-op:
/// backward compatibility for callers that pinned the full tool list.
#[test]
fn tools_validate_without_require_output_is_a_noop() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    let skill = contract_skill(cwd);
    write(&cwd.join("prompt.md"), "Do nothing.");
    write(
        &cwd.join("fixture.json"),
        r#"{"responses": [{"content": [{"type": "text", "text": "ok"}], "stopReason": "stop"}]}"#,
    );

    let output = run_velites(
        cwd,
        &[
            "--stub-fixture",
            "fixture.json",
            "--tools",
            "read,write,bash,validate",
            "--skill",
            &skill.to_string_lossy(),
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = parse_events(&output.stdout);
    assert!(
        events.iter().all(|e| e["type"] != "tool_execution_end"),
        "no tool rounds expected"
    );
}
