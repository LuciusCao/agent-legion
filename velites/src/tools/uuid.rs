//! `uuid` tool: UUID generation and validation (#442).
//!
//! Models hand-roll UUIDs badly (non-hex digits, wrong version/variant
//! nibbles, `123456abcdef`-style fakes); this tool gives them a reliable way
//! to mint and check identifiers instead of improvising.
//!
//! `generate` is deliberately NON-deterministic across calls: replaying a run
//! produces different values. The tool description tells the model to persist
//! generated values into artifacts rather than expect reproducibility.

use serde_json::Value;
use uuid::{Uuid, Variant};

use super::{truncate, ToolContext, ToolError, ToolOutput};

/// Upper bound for one `generate` call — enough for batch content, small
/// enough that a confused model cannot ask for an unbounded stream.
const MAX_GENERATE: usize = 100;
/// Upper bound for one `validate` call.
const MAX_VALIDATE: usize = 1000;
/// Upper bound for one `validate` value — a URN-prefixed UUID is 45 chars;
/// 512 is already generous. Longer inputs are caller bugs (e.g. a pasted
/// file fragment), and newlines would break the one-verdict-per-line
/// protocol, so both are rejected as invalid arguments.
const MAX_VALUE_CHARS: usize = 512;

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
        if raw.chars().count() > MAX_VALUE_CHARS || raw.chars().any(char::is_control) {
            return Err(ToolError::InvalidArgs(format!(
                "`values` entries must be at most {MAX_VALUE_CHARS} chars and contain no control characters"
            )));
        }
        lines.push(verdict(raw, &mut any_invalid));
    }
    let text = lines.join("\n");
    // The all-tools truncation contract (design §8) applies here too: verdict
    // lines are bounded per entry but 1000 of them still pass 50KB, so the
    // joined output goes through the same head truncation as `read`.
    let truncation = truncate::truncate_head(&text);
    let content = if truncation.truncated {
        format!(
            "{}\n\n[Showing {} of {} verdicts ({} total). Split `values` into smaller batches.]",
            truncation.content,
            truncation.output_lines,
            truncation.total_lines,
            truncate::format_size(truncation.total_bytes),
        )
    } else {
        truncation.content
    };
    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text { text: content }],
        is_error: any_invalid,
        // Volume semantics: pre-truncation, same as the other tools.
        output_bytes: text.len() as u64,
        // totalMs is filled by the ToolKind::execute dispatch boundary (#469).
        timing: None,
    })
}

fn verdict(raw: &str, any_invalid: &mut bool) -> String {
    match Uuid::parse_str(raw) {
        Ok(uuid) => {
            // Parsing success is not validity: nil/max UUIDs and other
            // non-RFC4122 variants parse fine but are exactly the kind of
            // hand-rolled fakes this tool exists to catch.
            if uuid.get_variant() != Variant::RFC4122 {
                *any_invalid = true;
                return format!(
                    "{raw}: invalid (non-RFC4122 variant, v{})",
                    uuid.get_version_num()
                );
            }
            // A correct variant is still not validity: RFC4122-variant values
            // with an undefined version nibble (0, or 9–15 outside the
            // RFC4122 1–5 / RFC9562 6–8 range) parse fine — e.g.
            // 00000000-0000-0000-8000-000000000000 — and `get_version`
            // reports exactly those as None.
            if uuid.get_version().is_none() {
                *any_invalid = true;
                return format!(
                    "{raw}: invalid (undefined version, v{})",
                    uuid.get_version_num()
                );
            }
            let mut notes = vec![format!("v{}", uuid.get_version_num())];
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
