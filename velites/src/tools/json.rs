//! `json` tool: field-level read/modify/write for JSON files (#518).
//!
//! Models repeatedly patch one nested field of a large JSON artifact they
//! themselves produced (fix `key_info_spec.json` entry content, delete one
//! `interactive_analysis.json` step key). Whole-file rewrites cost tokens
//! and introduce fresh errors, so models fell back to hand-written python
//! in bash heredocs — a stability hazard under concurrent execution. This
//! tool makes read-modify-write one primitive: `get` reads one JSON path,
//! `set`/`delete` mutate it and write the file back atomically (tmp +
//! rename, same as `write`). The `write` tool keeps its whole-file
//! semantics; this one is the complement, not a replacement.

use std::path::PathBuf;

use serde_json::Value;

use super::{resolve_in_cwd, truncate, ToolContext, ToolError, ToolOutput};

/// Upper bound for one path expression — a legit nested path is a few
/// dozen chars; anything longer is a pasted fragment, rejected early.
const MAX_PATH_CHARS: usize = 512;

pub async fn run(args: &Value, ctx: &ToolContext) -> ToolOutput {
    match run_inner(args, ctx) {
        Ok(output) => output,
        // Argument-shape failures reject before any filesystem work and
        // stay timing-free; resolution/IO failures happen mid-measurement
        // and keep their totalMs (#469), same convention as `write`.
        Err(err @ ToolError::InvalidArgs(_)) => ToolOutput::error(err.to_string()),
        Err(err) => ToolOutput::error(err.to_string()).measured(),
    }
}

fn run_inner(args: &Value, ctx: &ToolContext) -> Result<ToolOutput, ToolError> {
    let op = args
        .get("op")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `op`".into()))?;
    let path = args
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `path`".into()))?;
    let query = args
        .get("query")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `query`".into()))?;
    if query.chars().count() > MAX_PATH_CHARS {
        return Err(ToolError::InvalidArgs(format!(
            "`query` must be at most {MAX_PATH_CHARS} chars"
        )));
    }
    match op {
        "get" => get(ctx, path, query),
        "set" => {
            let value = args
                .get("value")
                .cloned()
                .ok_or_else(|| ToolError::InvalidArgs("missing field `value`".into()))?;
            set(ctx, path, query, value)
        }
        "delete" => delete(ctx, path, query),
        other => Err(ToolError::InvalidArgs(format!(
            "unknown op `{other}` (expected `get`, `set` or `delete`)"
        ))),
    }
}

/// Resolve one file against the sandbox and parse it as JSON.
fn load_json(ctx: &ToolContext, path: &str) -> Result<(PathBuf, Value), ToolError> {
    let resolved = resolve_in_cwd(&ctx.cwd, path)?;
    let raw = std::fs::read_to_string(&resolved)?;
    let value: Value = serde_json::from_str(&raw)
        .map_err(|err| ToolError::InvalidArgs(format!("{path} is not valid JSON: {err}")))?;
    Ok((resolved, value))
}

/// Serialize the whole document back and atomically replace the file
/// (tmp + rename, same protocol as the `write` tool).
fn store_json(resolved: &std::path::Path, root: &Value, path: &str) -> Result<u64, ToolError> {
    let text = serde_json::to_string_pretty(root)
        .map_err(|err| ToolError::InvalidArgs(format!("re-serializing {path} failed: {err}")))?;
    let tmp = resolved.with_file_name(format!(
        "{}.velites-tmp",
        resolved
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("velites-tmp")
    ));
    let write_result = std::fs::write(&tmp, &text).and_then(|_| std::fs::rename(&tmp, resolved));
    if let Err(err) = write_result {
        let _ = std::fs::remove_file(&tmp);
        return Err(err.into());
    }
    Ok(text.len() as u64)
}

fn get(ctx: &ToolContext, path: &str, query: &str) -> Result<ToolOutput, ToolError> {
    let (_resolved, root) = load_json(ctx, path)?;
    let found = query_json(&root, query)?;
    let text = match found {
        Some(value) => serde_json::to_string_pretty(value).map_err(|err| {
            ToolError::InvalidArgs(format!("serializing the queried value failed: {err}"))
        })?,
        None => "null".to_string(),
    };
    // The all-tools truncation contract (design §8): a queried subtree can
    // still be large, so the head truncation applies like `read`.
    let truncation = truncate::truncate_head(&text);
    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text {
            text: truncation.content,
        }],
        is_error: false,
        output_bytes: text.len() as u64,
        timing: None,
    })
}

fn set(ctx: &ToolContext, path: &str, query: &str, value: Value) -> Result<ToolOutput, ToolError> {
    let (resolved, mut root) = load_json(ctx, path)?;
    // descend to the PARENT of the last segment, then set/delete there.
    let parent = descend_mut(&mut root, query, path)?;
    let last = last_segment(query, path)?;
    match parent {
        Value::Object(map) => {
            map.insert(last.key, value.clone());
        }
        Value::Array(items) => {
            let index = last.index.ok_or_else(|| {
                ToolError::InvalidArgs(format!("`{query}` targets an array without an index"))
            })?;
            if index >= items.len() {
                return Err(ToolError::InvalidArgs(format!(
                    "`{query}` index {index} is out of bounds (len {})",
                    items.len()
                )));
            }
            items[index] = value.clone();
        }
        _ => {
            return Err(ToolError::InvalidArgs(format!(
                "`{query}` walks through a scalar; cannot set inside it"
            )))
        }
    }
    let bytes = store_json(&resolved, &root, path)?;
    Ok(ToolOutput::text(
        format!("set `{query}` in {path} (file now {bytes} bytes)"),
        false,
    ))
}

fn delete(ctx: &ToolContext, path: &str, query: &str) -> Result<ToolOutput, ToolError> {
    let (resolved, mut root) = load_json(ctx, path)?;
    let parent = descend_mut(&mut root, query, path)?;
    let last = last_segment(query, path)?;
    let removed = match parent {
        Value::Object(map) => map.remove(&last.key),
        Value::Array(items) => {
            let index = last.index.ok_or_else(|| {
                ToolError::InvalidArgs(format!("`{query}` targets an array without an index"))
            })?;
            if index >= items.len() {
                return Err(ToolError::InvalidArgs(format!(
                    "`{query}` index {index} is out of bounds (len {})",
                    items.len()
                )));
            }
            Some(items.remove(index))
        }
        _ => {
            return Err(ToolError::InvalidArgs(format!(
                "`{query}` walks through a scalar; cannot delete inside it"
            )))
        }
    };
    if removed.is_none() {
        return Err(ToolError::InvalidArgs(format!(
            "`{query}` does not exist in {path}"
        )));
    }
    let bytes = store_json(&resolved, &root, path)?;
    Ok(ToolOutput::text(
        format!("deleted `{query}` from {path} (file now {bytes} bytes)"),
        false,
    ))
}

/// One segment of a parsed path: an object key or an array index.
#[derive(Debug, PartialEq)]
struct Segment {
    key: String,
    index: Option<usize>,
}

/// Walk every segment but the last, leaving `cursor` at the parent.
/// Non-existent intermediate keys are an error for writes (we never
/// auto-vivify — the model should `set` parents explicitly or use
/// `write`); `get` tolerates them as absent via `query_json`.
fn descend_mut<'a>(
    cursor: &'a mut Value,
    query: &str,
    path: &str,
) -> Result<&'a mut Value, ToolError> {
    let segments = parse_path(query, path)?;
    let Some((_last, parents)) = segments.split_last() else {
        return Err(ToolError::InvalidArgs("empty `query`".into()));
    };
    let mut cursor = cursor;
    for segment in parents {
        cursor = match cursor {
            Value::Object(map) => map.get_mut(&segment.key).ok_or_else(|| {
                ToolError::InvalidArgs(format!(
                    "`{query}`: key `{}` not found in {path}",
                    segment.key
                ))
            })?,
            Value::Array(items) => {
                let index = segment.index.ok_or_else(|| {
                    ToolError::InvalidArgs(format!(
                        "`{query}`: array needs an index, got bare key `{}`",
                        segment.key
                    ))
                })?;
                let len = items.len();
                items.get_mut(index).ok_or_else(|| {
                    ToolError::InvalidArgs(format!(
                        "`{query}`: index {index} out of bounds (len {len})"
                    ))
                })?
            }
            _ => {
                return Err(ToolError::InvalidArgs(format!(
                    "`{query}` walks through a scalar; cannot descend into it"
                )))
            }
        };
    }
    Ok(cursor)
}

/// Resolve the last segment of the path (the set/delete target).
fn last_segment(query: &str, path: &str) -> Result<Segment, ToolError> {
    let segments = parse_path(query, path)?;
    segments
        .into_iter()
        .next_back()
        .ok_or_else(|| ToolError::InvalidArgs("empty `query`".into()))
}

/// Read-only query: walks the path, `None` when any segment is absent
/// (`get` reports null rather than failing — reads are forgiving).
fn query_json<'a>(root: &'a Value, query: &str) -> Result<Option<&'a Value>, ToolError> {
    let segments = parse_path(query, "(root)")?;
    let mut cursor = root;
    for segment in segments {
        cursor = match cursor {
            Value::Object(map) => match map.get(&segment.key) {
                Some(next) => next,
                None => return Ok(None),
            },
            Value::Array(items) => {
                let Some(index) = segment.index else {
                    return Ok(None);
                };
                match items.get(index) {
                    Some(next) => next,
                    None => return Ok(None),
                }
            }
            _ => return Ok(None),
        };
    }
    Ok(Some(cursor))
}

/// Parse `a.b[0].c` / `["a b"][2]` / `a['b.c']` into segments. Quoted keys
/// may contain dots and brackets; bare keys may not.
fn parse_path(query: &str, path: &str) -> Result<Vec<Segment>, ToolError> {
    let mut segments = Vec::new();
    let chars: Vec<char> = query.chars().collect();
    let mut i = 0;
    // Whether the next bare/quoted key is REQUIRED (start of query or right
    // after a `.` separator). Doubled/leading/trailing dots leave it true at
    // the end or hit it mid-query — both are empty-key forms, rejected.
    let mut expect_segment = true;
    while i < chars.len() {
        match chars[i] {
            '.' => {
                if expect_segment {
                    return Err(bad_path(query, path));
                }
                expect_segment = true;
                i += 1;
            }
            '[' => {
                i += 1;
                if i >= chars.len() {
                    return Err(bad_path(query, path));
                }
                if chars[i] == '"' || chars[i] == '\'' {
                    let quote = chars[i];
                    i += 1;
                    let mut key = String::new();
                    while i < chars.len() && chars[i] != quote {
                        if chars[i] == '\\' && i + 1 < chars.len() {
                            i += 1;
                        }
                        key.push(chars[i]);
                        i += 1;
                    }
                    if i >= chars.len() || chars[i] != quote {
                        return Err(bad_path(query, path));
                    }
                    i += 1; // closing quote
                    if i >= chars.len() || chars[i] != ']' {
                        return Err(bad_path(query, path));
                    }
                    i += 1; // ]
                    segments.push(Segment { key, index: None });
                    expect_segment = false;
                } else {
                    let mut digits = String::new();
                    while i < chars.len() && chars[i].is_ascii_digit() {
                        digits.push(chars[i]);
                        i += 1;
                    }
                    if digits.is_empty() || i >= chars.len() || chars[i] != ']' {
                        return Err(bad_path(query, path));
                    }
                    i += 1; // ]
                    segments.push(Segment {
                        key: digits.clone(),
                        index: Some(digits.parse::<usize>().map_err(|_| bad_path(query, path))?),
                    });
                    expect_segment = false;
                }
            }
            c => {
                let mut key = String::new();
                while i < chars.len() && chars[i] != '.' && chars[i] != '[' {
                    if c.is_whitespace() {
                        return Err(bad_path(query, path));
                    }
                    key.push(chars[i]);
                    i += 1;
                }
                if key.is_empty() {
                    return Err(bad_path(query, path));
                }
                segments.push(Segment { key, index: None });
                expect_segment = false;
            }
        }
    }
    if segments.is_empty() || expect_segment {
        // expect_segment still true = trailing dot (`a.`).
        return Err(bad_path(query, path));
    }
    Ok(segments)
}

fn bad_path(query: &str, path: &str) -> ToolError {
    ToolError::InvalidArgs(format!(
        "`{query}` is not a valid JSON path in {path} (expected forms like `a.b`, `steps[2].content`, `[\"a key\"].sub`)"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx(cwd: &std::path::Path) -> ToolContext {
        ToolContext {
            cwd: cwd.canonicalize().unwrap(),
            cancel: crate::cancel::CancelToken::default(),
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
            crate::events::ContentBlock::Text { text } => text.clone(),
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
        let output = run(&args("get", "entries[0].content", None), &ctx(dir.path())).await;
        assert!(!output.is_error);
        assert_eq!(text(&output), "\"old\"");
    }

    #[tokio::test]
    async fn get_missing_path_reports_null_not_error() {
        let dir = tempfile::tempdir().unwrap();
        write_spec(dir.path());
        let output = run(&args("get", "entries[9].missing", None), &ctx(dir.path())).await;
        assert!(!output.is_error);
        assert_eq!(text(&output), "null");
    }

    #[tokio::test]
    async fn set_rewrites_one_field_and_keeps_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        write_spec(dir.path());
        let output = run(
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
        run(
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
        let output = run(&args("delete", "entries[0].kept", None), &ctx(dir.path())).await;
        assert!(!output.is_error);
        let updated = read_spec(dir.path());
        assert!(updated["entries"][0].get("kept").is_none());
        assert_eq!(updated["entries"][0]["content"], "old");
    }

    #[tokio::test]
    async fn delete_array_element_shifts_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        write_spec(dir.path());
        run(&args("delete", "entries[0]", None), &ctx(dir.path())).await;
        let updated = read_spec(dir.path());
        assert_eq!(updated["entries"].as_array().unwrap().len(), 1);
        assert_eq!(updated["entries"][0]["content"], "second");
    }

    #[tokio::test]
    async fn delete_missing_field_is_an_invalid_args_error() {
        let dir = tempfile::tempdir().unwrap();
        write_spec(dir.path());
        let output = run(&args("delete", "entries[0].absent", None), &ctx(dir.path())).await;
        assert!(output.is_error);
        assert!(text(&output).contains("does not exist"));
    }

    #[tokio::test]
    async fn set_missing_intermediate_key_is_rejected_without_autovivify() {
        let dir = tempfile::tempdir().unwrap();
        write_spec(dir.path());
        let output = run(
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
        let output = run(&args("get", "a", None), &ctx(dir.path())).await;
        assert!(output.is_error);
        assert!(text(&output).contains("not valid JSON"));
    }

    #[tokio::test]
    async fn quoted_keys_may_contain_dots() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("spec.json"), r#"{"a.b": {"c": 1}}"#).unwrap();
        let output = run(&args("get", r#"["a.b"].c"#, None), &ctx(dir.path())).await;
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
        let output = run(&payload, &ctx(dir.path())).await;
        assert!(output.is_error);
        assert!(text(&output).contains("escapes the working directory"));
    }

    #[test]
    fn parse_path_forms() {
        let segments = parse_path("steps[2].content", "f").unwrap();
        assert_eq!(
            segments,
            vec![
                Segment {
                    key: "steps".into(),
                    index: None
                },
                Segment {
                    key: "2".into(),
                    index: Some(2)
                },
                Segment {
                    key: "content".into(),
                    index: None
                },
            ]
        );
        let segments = parse_path(r#"["a b"]['c.d']"#, "f").unwrap();
        assert_eq!(
            segments,
            vec![
                Segment {
                    key: "a b".into(),
                    index: None
                },
                Segment {
                    key: "c.d".into(),
                    index: None
                },
            ]
        );
        assert!(parse_path("", "f").is_err());
        assert!(parse_path("a[", "f").is_err());
        assert!(parse_path("a[x]", "f").is_err());
        assert!(parse_path("a..b", "f").is_err());
        assert!(parse_path(".a", "f").is_err());
        assert!(parse_path("a.", "f").is_err());
    }

    #[test]
    fn parse_path_rejects_empty_keys() {
        assert!(parse_path("a..b", "f").is_err());
        assert!(parse_path("..", "f").is_err());
    }
}
