//! Core `json` tool read/modify/write coverage through the public dispatch
//! surface. Private path-parser coverage remains beside the implementation.

use serde_json::Value;
use velites::tools::{ToolContext, ToolKind, ToolOutput};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    }
}

fn write_spec(dir: &std::path::Path) {
    std::fs::write(
        dir.join("spec.json"),
        r#"{"entries": [{"content": "old", "kept": true}, {"content": "second"}], "meta": {"version": 1}}"#,
    )
    .unwrap();
}

fn text(output: &ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

fn read_spec(dir: &std::path::Path) -> Value {
    serde_json::from_str(&std::fs::read_to_string(dir.join("spec.json")).unwrap()).unwrap()
}

fn args(op: &str, query: &str, value: Option<Value>) -> Value {
    let mut payload = serde_json::json!({"op": op, "path": "spec.json", "query": query});
    if let Some(value) = value {
        payload["value"] = value;
    }
    payload
}

#[tokio::test]
async fn get_reads_a_nested_field() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(&args("get", "entries[0].content", None), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    assert_eq!(text(&output), "\"old\"");
}

#[tokio::test]
async fn get_missing_path_reports_null_not_error() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(&args("get", "entries[9].missing", None), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    assert_eq!(text(&output), "null");
}

#[tokio::test]
async fn set_rewrites_one_field_and_keeps_the_rest() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(
            &args(
                "set",
                "entries[0].content",
                Some(serde_json::json!("corrected")),
            ),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let updated = read_spec(dir.path());
    assert_eq!(updated["entries"][0]["content"], "corrected");
    assert_eq!(updated["entries"][0]["kept"], true);
    assert_eq!(updated["entries"][1]["content"], "second");
    assert_eq!(updated["meta"]["version"], 1);
}

#[tokio::test]
async fn set_accepts_structured_values() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    ToolKind::Json
        .execute(
            &args(
                "set",
                "meta",
                Some(serde_json::json!({"version": 2, "note": "bumped"})),
            ),
            &ctx(dir.path()),
        )
        .await;
    let updated = read_spec(dir.path());
    assert_eq!(updated["meta"]["version"], 2);
    assert_eq!(updated["meta"]["note"], "bumped");
}

#[tokio::test]
async fn delete_removes_one_field() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(&args("delete", "entries[0].kept", None), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    let updated = read_spec(dir.path());
    assert!(updated["entries"][0].get("kept").is_none());
    assert_eq!(updated["entries"][0]["content"], "old");
}

#[tokio::test]
async fn delete_array_element_shifts_the_rest() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    ToolKind::Json
        .execute(&args("delete", "entries[0]", None), &ctx(dir.path()))
        .await;
    let updated = read_spec(dir.path());
    assert_eq!(updated["entries"].as_array().unwrap().len(), 1);
    assert_eq!(updated["entries"][0]["content"], "second");
}

#[tokio::test]
async fn delete_missing_field_is_an_invalid_args_error() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(&args("delete", "entries[0].absent", None), &ctx(dir.path()))
        .await;
    assert!(output.is_error);
    assert!(text(&output).contains("does not exist"));
}

#[tokio::test]
async fn set_missing_intermediate_key_is_rejected_without_autovivify() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(
            &args("set", "absent.key", Some(serde_json::json!(1))),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error);
    assert!(text(&output).contains("not found"));
    // 文件未被改动。
    assert_eq!(read_spec(dir.path())["meta"]["version"], 1);
}

#[tokio::test]
async fn malformed_json_file_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("spec.json"), "{not json").unwrap();
    let output = ToolKind::Json
        .execute(&args("get", "a", None), &ctx(dir.path()))
        .await;
    assert!(output.is_error);
    assert!(text(&output).contains("not valid JSON"));
}

#[tokio::test]
async fn quoted_keys_may_contain_dots() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("spec.json"), r#"{"a.b": {"c": 1}}"#).unwrap();
    let output = ToolKind::Json
        .execute(&args("get", r#"["a.b"].c"#, None), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    assert_eq!(text(&output), "1");
}

#[tokio::test]
async fn file_path_escapes_are_rejected_by_the_sandbox() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    // 沙箱防线在 file path（与 write 工具同一 resolve_in_cwd）——
    // `query` 只是 key 序列，不是文件系统路径。
    let mut payload = serde_json::json!({"op": "get", "query": "a"});
    payload["path"] = "../outside.json".into();
    let output = ToolKind::Json.execute(&payload, &ctx(dir.path())).await;
    assert!(output.is_error);
    assert!(text(&output).contains("escapes the working directory"));
}
