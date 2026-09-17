//! `json` tool integration tests (#518): the read-modify-write primitive
//! driven end-to-end through the real binary with the stub provider —
//! the scenario the issue describes (patch one field of a large JSON
//! artifact, no whole-file rewrite, no bash heredoc python).

use std::os::fd::FromRawFd;
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

#[test]
fn json_tool_oversized_file_is_rejected_before_loading() {
    // #637: load_json's whole-file read_to_string is size-bounded — an
    // over-cap JSON file (5 MiB against the 4 MiB limit) fails fast at the
    // metadata check with a bash-extraction hint, before any byte is read
    // into memory or parsed.
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    let mut content = vec![b'x'; 5 * 1024 * 1024];
    content.push(b'\n');
    std::fs::write(cwd.join("big.json"), &content).expect("failed to write test file");
    write(&cwd.join("prompt.md"), "Query the spec.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "json", "arguments": {"op": "get", "path": "big.json", "query": "a"}}]},
    {"content": [{"type": "text", "text": "done"}], "stopReason": "stop"}
  ]
}"#,
    );

    let output = run_velites(cwd, "read,write,bash,json");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = tool_events(&output.stdout);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["isError"], true);
    let text = events[0]["result"]["content"][0]["text"].as_str().unwrap();
    assert!(
        text.contains("too large"),
        "missing size-limit error: {text}"
    );
    assert!(
        text.contains("Extract the needed fields via bash"),
        "missing extraction hint: {text}"
    );
}

// #689 攻击报告 HIGH-2/3 复测钉子：读后放大（Value 树 + pretty 重序列化）
// 与 set 的 value 无上限，都在 4 MiB 读侧 cap 之外。这里是预算行为的空间
// 钉子（内存峰值复测见修复报告）；载荷与攻击报告同构。
use velites::tools::{ToolContext, ToolKind};

fn inprocess_ctx(cwd: &Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    }
}

fn inprocess_text(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

// HIGH-2 树侧：4 MiB 内的全数字文件（最密形态，一个字节一个节点）把
// Value 树撑到字节数的 ~33x——节点预算必须在 pretty/输出之前拒绝。
#[tokio::test]
async fn json_tool_rejects_over_budget_trees_before_any_output() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    // 300,004 nodes (root + entry + array + 300,001 numbers) at ~600 KB —
    // comfortably inside the 4 MiB byte cap, over the 300k node budget.
    let dense = format!("{{\"k\":[{}]}}", vec!["1"; 300_001].join(","));
    std::fs::write(cwd.join("dense.json"), &dense).unwrap();

    for op in ["get", "set"] {
        let mut args = serde_json::json!({"op": op, "path": "dense.json", "query": "k"});
        if op == "set" {
            args["value"] = serde_json::json!(1);
        }
        let output = ToolKind::Json.execute(&args, &inprocess_ctx(cwd)).await;
        assert!(output.is_error, "{op} must reject the over-budget tree");
        let text = inprocess_text(&output);
        assert!(
            text.contains("content too large for in-memory processing"),
            "missing the TooLarge prefix: {text}"
        );
        assert!(
            text.contains("300000 nodes"),
            "missing the node-budget rejection: {text}"
        );
    }
}

// HIGH-2 写侧：深嵌套链在节点与字节预算内、compact 300 KB，pretty 展开
// 却到 ~36 MB——pretty 输出预算在序列化途中封顶（瞬态字符串同受预算）。
// 查询命中携带整棵数组的字段（`k`），而非单条链。
#[tokio::test]
async fn json_tool_get_caps_pretty_width_amplification() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    // 1240 chains of depth 120: ~299k nodes, ~300 KB compact, ~36 MB pretty.
    let chain = format!("{}0{}", "[".repeat(120), "]".repeat(120));
    let dense = format!("{{\"k\":[{}]}}", vec![chain.as_str(); 1240].join(","));
    assert!(
        dense.len() < 4 * 1024 * 1024,
        "fixture must be under the byte cap"
    );
    std::fs::write(cwd.join("deep.json"), &dense).unwrap();

    let output = ToolKind::Json
        .execute(
            &serde_json::json!({"op": "get", "path": "deep.json", "query": "k"}),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(output.is_error, "the wide pretty output must be rejected");
    let text = inprocess_text(&output);
    assert!(
        text.contains("content too large for in-memory processing"),
        "missing the TooLarge prefix: {text}"
    );
    assert!(
        text.contains("8.0MB"),
        "missing the output-budget rejection: {text}"
    );
    assert!(
        text.contains("too large for the json tool"),
        "the error must name the file and the budget: {text}"
    );
}

// HIGH-3：value 只受上游 32 MiB arguments cap 约束——超整文件上限的
// value 必须在树被改动、文件被写回之前拒绝；合法的较大 value 仍然可用。
#[tokio::test]
async fn json_tool_set_caps_value_size() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write_spec(cwd);

    // Over the byte budget: a 4 MiB + 1 string.
    let oversized = serde_json::json!("x".repeat(4 * 1024 * 1024 + 1));
    let output = ToolKind::Json
        .execute(
            &serde_json::json!({
                "op": "set", "path": "key_info_spec.json",
                "query": "entries[0].content", "value": oversized
            }),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(output.is_error, "an over-cap value must be rejected");
    let text = inprocess_text(&output);
    assert!(
        text.contains("content too large for in-memory processing"),
        "missing the TooLarge prefix: {text}"
    );
    assert!(
        text.contains("4MB"),
        "missing the value-cap wording: {text}"
    );
    assert!(
        text.contains("whole-file limit"),
        "the value cap is the whole-file limit: {text}"
    );
    // The file is untouched.
    let spec: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(cwd.join("key_info_spec.json")).unwrap())
            .unwrap();
    assert_eq!(spec["entries"][0]["content"], "needs fixing");

    // Over the node budget: a 300k+1 element array.
    let output = ToolKind::Json
        .execute(
            &serde_json::json!({
                "op": "set", "path": "key_info_spec.json",
                "query": "entries[0].content", "value": vec![1; 300_001]
            }),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(output.is_error);
    assert!(inprocess_text(&output).contains("300000 nodes"));

    // Positive control: a large-but-legal value (1 MiB string) still lands.
    let legal = serde_json::json!("y".repeat(1024 * 1024));
    let output = ToolKind::Json
        .execute(
            &serde_json::json!({
                "op": "set", "path": "key_info_spec.json",
                "query": "entries[0].content", "value": legal
            }),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(!output.is_error, "a 1 MiB value is within the cap");
    let spec: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(cwd.join("key_info_spec.json")).unwrap())
            .unwrap();
    assert_eq!(
        spec["entries"][0]["content"].as_str().unwrap().len(),
        1024 * 1024
    );
}

// #518 行为面（跟上）：写回现在是 compact——文件保持一次 `get` 可读，
// 且同一次 set 的确认文案报出落盘字节数。
#[tokio::test]
async fn json_tool_set_writes_back_compact_json() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    write_spec(cwd);
    let output = ToolKind::Json
        .execute(
            &serde_json::json!({
                "op": "set", "path": "key_info_spec.json",
                "query": "meta.version", "value": 2
            }),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(!output.is_error);
    let on_disk = std::fs::read_to_string(cwd.join("key_info_spec.json")).unwrap();
    assert!(
        !on_disk.contains("\n"),
        "set must write compact JSON, got pretty: {on_disk}"
    );
    assert_eq!(
        on_disk,
        serde_json::to_string(&serde_json::from_str::<serde_json::Value>(&on_disk).unwrap())
            .unwrap()
    );
    // The compact file still round-trips through get.
    let output = ToolKind::Json
        .execute(
            &serde_json::json!({"op": "get", "path": "key_info_spec.json", "query": "meta"}),
            &inprocess_ctx(cwd),
        )
        .await;
    assert!(!output.is_error);
    assert_eq!(inprocess_text(&output), "{\n  \"version\": 2\n}");
}

// #689 review P1: the metadata().len() pre-check is bypassable — a FIFO
// reports len() == 0 forever, so the old unbounded read_to_string inside
// load_json sailed past the cap to EOF. The cap must be enforced by the
// read itself (take(cap+1)). This test drives a >4 MiB JSON stream through
// a named pipe into the json tool and pins the rejection.
#[cfg(unix)]
#[test]
fn json_tool_fifo_over_cap_is_rejected_by_the_bounded_read() {
    let dir = tempfile::tempdir().unwrap();
    let cwd = dir.path();
    let fifo = cwd.join("pipe.json");
    let cpath = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);

    // A >4 MiB JSON document via the fifo: valid UTF-8, starts as JSON, and
    // (irrelevantly for the cap) is never fully parseable — the read must be
    // rejected on SIZE before parsing happens.
    let total = 5 * 1024 * 1024usize;
    let writer = std::thread::spawn(move || {
        use std::io::Write;
        let flags = libc::O_WRONLY | libc::O_NONBLOCK;
        let cpath = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
        let fd = loop {
            let fd = unsafe { libc::open(cpath.as_ptr(), flags) };
            if fd >= 0 {
                break fd;
            }
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ENXIO) {
                panic!("unexpected open error: {err}");
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        };
        let mut file = unsafe { std::fs::File::from_raw_fd(fd) };
        let chunk = vec![b'x'; 64 * 1024];
        if file.write_all(b"{\"data\": \"").is_err() {
            panic!("fifo header write failed");
        }
        let mut written = 0usize;
        let mut saw_epipe = false;
        while written < total {
            match file.write(&chunk[..(total - written).min(chunk.len())]) {
                Ok(n) => written += n,
                // A nonblocking write into a full pipe returns EAGAIN — retry.
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                // The reader stopped at cap+1 bytes and closed its end: the
                // bounded read is doing its job, the rejection is proven.
                Err(err) if err.kind() == std::io::ErrorKind::BrokenPipe => {
                    saw_epipe = true;
                    break;
                }
                Err(err) => panic!("fifo write failed: {err}"),
            }
        }
        // #689 review P2-2: the EPIPE is the OBSERVABLE proof that the read
        // side was bounded (closed its end at cap+1) rather than draining
        // the whole 5 MiB and rejecting on a length check afterwards. If the
        // take(cap+1) ever regresses to an unbounded read, the reader only
        // closes after EOF — the writer pushes all 5 MiB, never sees EPIPE,
        // and this assertion fails instead of the test quietly passing.
        drop(file); // close → any remaining reader sees EOF
        assert!(
            saw_epipe,
            "writer drained the full {total} bytes without EPIPE — the reader \
             never closed its end at the cap, i.e. the read was NOT bounded"
        );
        assert!(
            written <= 4 * 1024 * 1024 + 128 * 1024,
            "reader closed too late: wrote {written} bytes before EPIPE — the \
             bounded read must stop at cap+1 (plus pipe-buffer slack)"
        );
    });

    write(&cwd.join("prompt.md"), "Query the spec.");
    write(
        &cwd.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "json", "arguments": {"op": "get", "path": "pipe.json", "query": "a"}}]},
    {"content": [{"type": "text", "text": "done"}], "stopReason": "stop"}
  ]
}"#,
    );

    let output = run_velites(cwd, "read,write,bash,json");
    writer.join().expect("writer thread panicked");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = tool_events(&output.stdout);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["isError"], true);
    let text = events[0]["result"]["content"][0]["text"].as_str().unwrap();
    // Positive anchors on the TooLarge error's own wording: the error class
    // prefix ("content too large for in-memory processing", ToolError::TooLarge)
    // plus the FIFO branch's own text prove the SIZE rejection fired — NOT a
    // downstream JSON parse failure of an unbounded read. #689 review P3-2:
    // the old exclusion (`!contains("not valid JSON")`) silently lost
    // meaning if that message were ever reworded; a prefix anchor cannot.
    assert!(
        text.contains("content too large for in-memory processing"),
        "missing the ToolError::TooLarge prefix: {text}"
    );
    assert!(
        text.contains("exceeds the 4MB whole-file limit"),
        "missing the bounded-read size-limit error: {text}"
    );
    assert!(
        text.contains("growing/FIFO sources report no exact size"),
        "must not fake a precise size for a source whose len() is 0: {text}"
    );
    assert!(
        text.contains("Extract the needed fields via bash"),
        "missing extraction hint: {text}"
    );
}
