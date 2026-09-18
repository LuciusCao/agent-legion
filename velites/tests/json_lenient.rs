//! #747 json `set` lenient-parse integration tests, through the library
//! surface (`velites::tools::ToolKind::execute`) the same way embedders
//! drive it — the whole salvage decision (lossy-number round-trip gate,
//! size gate, note wording) pinned from the outside, without reaching
//! into private helpers.
//!
//! The `json.rs` inline `#[cfg(test)]` suite keeps the core
//! read/modify-write coverage; these #747 tests live as a sister
//! integration module because the review round grew them past the
//! file's raw-line budget (the move is also what the #747 exemption's
//! timebox asked for: test surface split out of the source file).

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

fn read_spec(dir: &std::path::Path) -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(dir.join("spec.json")).unwrap()).unwrap()
}

fn args(op: &str, query: &str, value: Option<serde_json::Value>) -> serde_json::Value {
    let mut payload = serde_json::json!({"op": op, "path": "spec.json", "query": query});
    if let Some(value) = value {
        payload["value"] = value;
    }
    payload
}

/// #747 正确传法：容器直接以 JSON 值传入——原样写入，输出不带解析
/// 注记（注记只在发生宽容解析时出现）。
#[tokio::test]
async fn set_container_value_passed_directly_is_written_verbatim() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(
            &args("set", "meta.tags", Some(serde_json::json!(["a", "b"]))),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = text(&output);
    assert!(!text.contains("parsed"), "got: {text}");
    let updated = read_spec(dir.path());
    assert_eq!(updated["meta"]["tags"], serde_json::json!(["a", "b"]));
    assert_eq!(updated["meta"]["version"], 1);
}

/// #747 二次编码：装着 JSON 容器文本的字符串（带首尾空白）——解析为
/// 容器写入，输出明说发生了改写与字面字符串的去处。
#[tokio::test]
async fn set_double_encoded_container_text_is_parsed_with_a_note() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let output = ToolKind::Json
        .execute(
            &args(
                "set",
                "entries[0].content",
                Some(serde_json::json!("  [\"fixed\", \"also fixed\"]  ")),
            ),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let note = text(&output);
    assert!(note.contains("parsed as an array"), "got: {note}");
    assert!(note.contains("`write` tool"), "got: {note}");
    let updated = read_spec(dir.path());
    assert_eq!(
        updated["entries"][0]["content"],
        serde_json::json!(["fixed", "also fixed"])
    );
    // 对象文本同理。
    let output = ToolKind::Json
        .execute(
            &args(
                "set",
                "entries[1].content",
                Some(serde_json::json!("{\"k\": 1}")),
            ),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    assert!(text(&output).contains("parsed as an object"));
    let updated = read_spec(dir.path());
    assert_eq!(updated["entries"][1]["content"]["k"], 1);
}

/// #747 对抗 review 的边界矩阵（P1-1 数字精度 / P2-3 长度闸 / P3-1
/// 边界形态）：每个形态钉住「字面 or 解析」与注记是否出现。全部走
/// `meta.<key>` 扁平 query，便于统一取值断言。
#[tokio::test]
async fn set_lenient_parse_boundary_matrix() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let lossy = "would not survive parsing losslessly";
    // (key, value, 应字面写入?, 期待注记子串；None = 应无任何注记)
    let cases: Vec<(&str, serde_json::Value, bool, Option<&str>)> = vec![
        // P1-1 reviewer 实测形态：17 位浮点取整 / 超 u64 大整数降级——
        // 不可无损往返，字面写入 + lossy 注记。
        (
            "a",
            serde_json::json!("[1.0000000000000001]"),
            true,
            Some(lossy),
        ),
        (
            "b",
            serde_json::json!("[123456789012345678901234567890]"),
            true,
            Some(lossy),
        ),
        // 科学计数形态同理：1e2 重序列化为 100.0，变形即拒绝。
        ("c", serde_json::json!("[1e2]"), true, Some(lossy)),
        // 三重编码：内层是带引号的字符串文本，不以 [/{ 开头——不是
        // 候选，字面写入、无注记。
        ("d", serde_json::json!("\"[1,2]\""), true, None),
        // 开头像容器、整体不是合法 JSON：不是候选，字面写入、无注记。
        ("e", serde_json::json!("[1, 2] extra"), true, None),
        // 空容器：可无损往返，解析为容器 + parsed 注记。
        (
            "f",
            serde_json::json!("[]"),
            false,
            Some("parsed as an array"),
        ),
        (
            "g",
            serde_json::json!("{}"),
            false,
            Some("parsed as an object"),
        ),
        // Unicode 空白（NBSP）容忍：trim 后是合法容器文本，解析 + 注记。
        (
            "h",
            serde_json::json!("\u{a0}[1]\u{a0}"),
            false,
            Some("parsed as an array"),
        ),
    ];
    for (key, value, expect_literal, expect_note) in cases {
        let query = format!("meta.{key}");
        let output = ToolKind::Json
            .execute(&args("set", &query, Some(value.clone())), &ctx(dir.path()))
            .await;
        assert!(!output.is_error, "{query}");
        let note = text(&output);
        let written = &read_spec(dir.path())["meta"][key];
        match expect_note {
            Some(expected) => assert!(note.contains(expected), "{query}: {note}"),
            None => assert!(
                !note.contains("parsed as") && !note.contains("looks like"),
                "{query} must carry no note, got: {note}"
            ),
        }
        if expect_literal {
            assert_eq!(written, &value, "{query} must stay the literal string");
        } else {
            assert_ne!(written, &value, "{query} must be parsed, not literal");
        }
    }
}

/// #747 P2-3 长度闸：超闸的容器文本字符串不尝试解析，字面写入 +
/// 过大注记（封死无界 parse + DOM 放大，#637 形态）。
#[tokio::test]
async fn set_oversized_container_text_skips_the_parse_attempt() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    let oversized = format!("[{}1]", "1,".repeat(150_000)); // ~300KB > 256KB 闸
    let output = ToolKind::Json
        .execute(
            &args("set", "meta.oversized", Some(serde_json::json!(oversized))),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let note = text(&output);
    assert!(note.contains("lenient-parse gate"), "got: {note}");
    assert!(note.contains("256KB"), "got: {note}");
    // 字面写入：文件里是原字符串本身。
    let raw = read_spec(dir.path())["meta"]["oversized"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(raw.starts_with("[1,1,"));
    assert!(raw.ends_with(",1]"));
    assert_eq!(raw.len(), 300_003);
}

/// #747 字面字符串场景：不以 `[`/`{` 开头或不可解析的 JSON-looking
/// 文本、标量形态字符串——一律按字面写入，不触发宽容解析。
#[tokio::test]
async fn set_json_looking_non_container_strings_stay_literal() {
    let dir = tempfile::tempdir().unwrap();
    write_spec(dir.path());
    for (query, value) in [
        ("entries[0].content", "[pending review]"),
        ("entries[1].content", "123"),
        ("meta.note", "true"),
    ] {
        let output = ToolKind::Json
            .execute(
                &args("set", query, Some(serde_json::json!(value))),
                &ctx(dir.path()),
            )
            .await;
        assert!(!output.is_error, "{query}");
        let text = text(&output);
        assert!(!text.contains("parsed"), "{query}: {text}");
    }
    let updated = read_spec(dir.path());
    assert_eq!(updated["entries"][0]["content"], "[pending review]");
    assert_eq!(updated["entries"][1]["content"], "123");
    assert_eq!(updated["meta"]["note"], "true");
    assert_eq!(updated["meta"]["version"], 1);
}
