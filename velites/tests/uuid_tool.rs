//! Uuid tool tests (#442): generate/validate semantics, arg validation, and
//! the fake-UUID detection the tool exists for.

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // The uuid tool touches no filesystem; the sandbox is irrelevant.
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    }
}

fn result_text(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

async fn run_uuid(args: serde_json::Value) -> velites::tools::ToolOutput {
    let dir = tempfile::tempdir().unwrap();
    ToolKind::Uuid.execute(&args, &ctx(dir.path())).await
}

#[tokio::test]
async fn generate_mints_canonical_v4_by_default() {
    let output = run_uuid(serde_json::json!({"op": "generate"})).await;
    assert!(!output.is_error);
    let uuid = uuid::Uuid::parse_str(&result_text(&output)).unwrap();
    assert_eq!(uuid.get_version_num(), 4);
    assert_eq!(uuid.get_variant(), uuid::Variant::RFC4122);
}

#[tokio::test]
async fn generate_count_and_v7() {
    let output = run_uuid(serde_json::json!({"op": "generate", "count": 5, "version": "v7"})).await;
    assert!(!output.is_error);
    let text = result_text(&output);
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 5);
    let parsed: Vec<uuid::Uuid> = lines
        .iter()
        .map(|line| uuid::Uuid::parse_str(line).unwrap())
        .collect();
    assert!(parsed.iter().all(|u| u.get_version_num() == 7));
    // All distinct: a model that duplicates one value defeats the point.
    let mut sorted = parsed.clone();
    sorted.sort();
    sorted.dedup();
    assert_eq!(sorted.len(), parsed.len());
}

#[tokio::test]
async fn generate_rejects_bad_args() {
    for args in [
        serde_json::json!({}),                                  // missing op
        serde_json::json!({"op": "mint"}),                      // unknown op
        serde_json::json!({"op": "generate", "count": 0}),      // too few
        serde_json::json!({"op": "generate", "count": 101}),    // too many
        serde_json::json!({"op": "generate", "count": "five"}), // wrong type
        serde_json::json!({"op": "generate", "version": "v1"}), // unknown version
    ] {
        let output = run_uuid(args).await;
        assert!(output.is_error, "args must be rejected: {output:?}");
    }
}

#[tokio::test]
async fn validate_accepts_real_uuids_with_version_report() {
    let output = run_uuid(serde_json::json!({
        "op": "validate",
        "values": [
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "018e4f7a-7c3a-7b8e-9f2d-5a1c3e6b8d0f"
        ]
    }))
    .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(
        text.contains("f47ac10b-58cc-4372-a567-0e02b2c3d479: ok (v4)"),
        "{text}"
    );
    assert!(
        text.contains("018e4f7a-7c3a-7b8e-9f2d-5a1c3e6b8d0f: ok (v7)"),
        "{text}"
    );
}

#[tokio::test]
async fn validate_flags_fake_uuids() {
    let output = run_uuid(serde_json::json!({
        "op": "validate",
        "values": [
            "123456abcdef",                                  // not even UUID-shaped
            "f47ac10b-58cc-4372-a567-0e02b2c3d47",           // one char short
            "g47ac10b-58cc-4372-a567-0e02b2c3d479",          // non-hex digit
            "00000000-0000-0000-0000-000000000000",          // nil: parses, wrong variant
            "ffffffff-ffff-ffff-ffff-ffffffffffff"           // max: parses, wrong variant
        ]
    }))
    .await;
    assert!(output.is_error, "any invalid value must flag the result");
    let text = result_text(&output);
    assert_eq!(
        text.lines().filter(|l| l.contains(": invalid")).count(),
        5,
        "{text}"
    );
    assert!(text.contains("non-RFC4122 variant"), "{text}");
}

#[tokio::test]
async fn validate_rejects_undefined_versions() {
    // RFC4122-variant values with version nibble 0 or 9–15 parse fine but
    // have no defined UUID version (RFC 4122 defines 1–5, RFC 9562 adds
    // 6–8). The uuid crate reports them via `get_version() == None`, and
    // this tool exists to catch exactly those wrong-version fakes. Note the
    // v15 case is not the max UUID — its variant bits are 8fff, not ffff.
    let output = run_uuid(serde_json::json!({
        "op": "validate",
        "values": [
            "00000000-0000-0000-8000-000000000000", // v0
            "99999999-9999-9999-8999-999999999999", // v9
            "aaaaaaaa-aaaa-aaaa-8aaa-aaaaaaaaaaaa", // v10
            "bbbbbbbb-bbbb-bbbb-8bbb-bbbbbbbbbbbb", // v11
            "cccccccc-cccc-cccc-8ccc-cccccccccccc", // v12
            "dddddddd-dddd-dddd-8ddd-dddddddddddd", // v13
            "eeeeeeee-eeee-eeee-8eee-eeeeeeeeeeee", // v14
            "ffffffff-ffff-ffff-8fff-ffffffffffff"  // v15
        ]
    }))
    .await;
    assert!(output.is_error, "undefined versions must flag the result");
    let text = result_text(&output);
    assert_eq!(
        text.lines().filter(|l| l.contains(": invalid")).count(),
        8,
        "{text}"
    );
    assert!(text.contains("undefined version"), "{text}");
}

#[tokio::test]
async fn validate_accepts_all_defined_versions() {
    // v1–v8 are the defined versions and must stay ok: `generate` only mints
    // v4/v7, but values from other systems (v1/v2 time-based, v3/v5 name
    // hashes, v6/v8 per RFC 9562) are legitimately valid identifiers.
    let values: Vec<String> = (1..=8)
        .map(|n| {
            let c = std::char::from_digit(n, 16).unwrap();
            let g = |len: usize| c.to_string().repeat(len);
            format!("{}-{}-{}{}-8{}-{}", g(8), g(4), c, g(3), g(3), g(12))
        })
        .collect();
    let output = run_uuid(serde_json::json!({"op": "validate", "values": values})).await;
    assert!(!output.is_error, "defined versions must be ok: {output:?}");
    let text = result_text(&output);
    for n in 1..=8 {
        let expected = format!(": ok (v{})", n);
        assert!(
            text.lines().any(|l| l.ends_with(&expected)),
            "expected a v{n} ok verdict: {text}"
        );
    }
}

#[tokio::test]
async fn validate_notes_non_canonical_but_well_formed() {
    // Uppercase parses fine but is not the canonical lowercase form; the note
    // exists so a model checking its own output learns the canonical spelling.
    let output = run_uuid(serde_json::json!({
        "op": "validate",
        "values": ["F47AC10B-58CC-4372-A567-0E02B2C3D479"]
    }))
    .await;
    assert!(!output.is_error);
    assert!(result_text(&output).contains("non-canonical form"));
}

#[tokio::test]
async fn validate_rejects_bad_args() {
    for args in [
        serde_json::json!({"op": "validate"}), // missing values
        serde_json::json!({"op": "validate", "values": []}), // empty
        serde_json::json!({"op": "validate", "values": [42]}), // non-string
        serde_json::json!({"op": "validate", "values": "not-array"}), // wrong type
    ] {
        let output = run_uuid(args).await;
        assert!(output.is_error, "args must be rejected: {output:?}");
    }
}

#[tokio::test]
async fn validate_rejects_oversized_or_control_char_values() {
    // A value longer than any real UUID representation (a URN is 45 chars;
    // the cap is a generous 512) is a caller bug, and a newline would break
    // the one-verdict-per-line output protocol — both are arg errors.
    for value in ["x".repeat(513), "abc\ndef".to_string()] {
        let output = run_uuid(serde_json::json!({"op": "validate", "values": [value]})).await;
        assert!(output.is_error, "value must be rejected: {output:?}");
    }
}

#[tokio::test]
async fn validate_output_is_head_truncated_with_note() {
    // 1000 valid v4s with non-canonical notes ≈ 66KB — over the 50KB
    // tool-output budget (design §8); truncation must kick in with a
    // batching hint, and output_bytes keeps the pre-truncation volume.
    let values: Vec<String> = (0..1000)
        .map(|_| uuid::Uuid::new_v4().to_string().to_uppercase())
        .collect();
    let output = run_uuid(serde_json::json!({"op": "validate", "values": values})).await;
    assert!(
        !output.is_error,
        "uppercase v4s are valid, just non-canonical"
    );
    let text = result_text(&output);
    assert!(text.contains("[Showing "), "{text}");
    assert!(text.contains("verdicts"), "{text}");
    assert!(output.output_bytes as usize > text.len());
}
