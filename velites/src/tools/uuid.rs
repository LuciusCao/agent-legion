//! `uuid` tool: deterministic UUID generation and validation (#442).
//!
//! Models hand-roll UUIDs badly (non-hex digits, wrong version/variant
//! nibbles, `123456abcdef`-style fakes); this tool gives them a deterministic
//! way to mint and check identifiers instead of improvising.
//!
//! `generate` is deliberately NON-deterministic across calls: replaying a run
//! produces different values. The tool description tells the model to persist
//! generated values into artifacts rather than expect reproducibility.

use serde_json::Value;
use uuid::{Uuid, Variant};

use super::{ToolContext, ToolError, ToolOutput};

/// Upper bound for one `generate` call — enough for batch content, small
/// enough that a confused model cannot ask for an unbounded stream.
const MAX_GENERATE: usize = 100;
/// Upper bound for one `validate` call.
const MAX_VALIDATE: usize = 1000;

pub async fn run(args: &Value, _ctx: &ToolContext) -> ToolOutput {
    match run_inner(args) {
        Ok(output) => output,
        Err(err) => ToolOutput::error(err.to_string()),
    }
}

fn run_inner(args: &Value) -> Result<ToolOutput, ToolError> {
    let op = args
        .get("op")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `op`".into()))?;
    match op {
        "generate" => generate(args),
        "validate" => validate(args),
        other => Err(ToolError::InvalidArgs(format!(
            "unknown op `{other}` (expected `generate` or `validate`)"
        ))),
    }
}

fn generate(args: &Value) -> Result<ToolOutput, ToolError> {
    let count = match args.get("count") {
        None => 1,
        Some(value) => value
            .as_u64()
            .ok_or_else(|| ToolError::InvalidArgs("`count` must be a positive integer".into()))?
            as usize,
    };
    if count == 0 || count > MAX_GENERATE {
        return Err(ToolError::InvalidArgs(format!(
            "`count` must be between 1 and {MAX_GENERATE}"
        )));
    }
    let version = args.get("version").and_then(Value::as_str).unwrap_or("v4");
    let text = (0..count)
        .map(|_| mint(version))
        .collect::<Result<Vec<_>, _>>()?
        .join("\n");
    Ok(ToolOutput::text(text, false))
}

fn mint(version: &str) -> Result<String, ToolError> {
    match version {
        "v4" => Ok(Uuid::new_v4().to_string()),
        "v7" => Ok(Uuid::now_v7().to_string()),
        other => Err(ToolError::InvalidArgs(format!(
            "unknown version `{other}` (expected `v4` or `v7`)"
        ))),
    }
}

fn validate(args: &Value) -> Result<ToolOutput, ToolError> {
    let values = args
        .get("values")
        .and_then(Value::as_array)
        .ok_or_else(|| ToolError::InvalidArgs("missing array field `values`".into()))?;
    if values.is_empty() || values.len() > MAX_VALIDATE {
        return Err(ToolError::InvalidArgs(format!(
            "`values` must contain between 1 and {MAX_VALIDATE} strings"
        )));
    }

    let mut lines = Vec::with_capacity(values.len());
    let mut any_invalid = false;
    for value in values {
        let raw = value
            .as_str()
            .ok_or_else(|| ToolError::InvalidArgs("`values` entries must be strings".into()))?;
        lines.push(verdict(raw, &mut any_invalid));
    }
    Ok(ToolOutput::text(lines.join("\n"), any_invalid))
}

fn verdict(raw: &str, any_invalid: &mut bool) -> String {
    match Uuid::parse_str(raw) {
        Ok(uuid) => {
            let mut notes = vec![format!("v{}", uuid.get_version_num())];
            if uuid.get_variant() != Variant::RFC4122 {
                notes.push("non-RFC4122 variant".to_string());
            }
            if raw != uuid.to_string() {
                notes.push("non-canonical form".to_string());
            }
            format!("{raw}: ok ({})", notes.join(", "))
        }
        Err(err) => {
            *any_invalid = true;
            format!("{raw}: invalid ({err})")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generate_defaults_to_one_v4() {
        let output = run_inner(&serde_json::json!({"op": "generate"})).unwrap();
        assert!(!output.is_error);
        let text = match &output.content[0] {
            crate::events::ContentBlock::Text { text } => text,
            other => panic!("expected text content, got {other:?}"),
        };
        let uuid = Uuid::parse_str(text).unwrap();
        assert_eq!(uuid.get_version_num(), 4);
        assert_eq!(uuid.get_variant(), Variant::RFC4122);
    }

    #[test]
    fn generate_rejects_out_of_range_count() {
        for count in [0, MAX_GENERATE + 1] {
            let err =
                run_inner(&serde_json::json!({"op": "generate", "count": count})).unwrap_err();
            assert!(matches!(err, ToolError::InvalidArgs(_)), "{err}");
        }
    }

    #[test]
    fn validate_marks_fake_uuids_invalid() {
        let output = run_inner(&serde_json::json!({
            "op": "validate",
            "values": ["f47ac10b-58cc-4372-a567-0e02b2c3d479", "123456abcdef"]
        }))
        .unwrap();
        assert!(output.is_error, "any invalid value must flag the output");
    }
}
