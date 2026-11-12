//! Registry parsing tests for `outputTokensParam` (#1093): which request
//! field the OpenAI-compatible provider uses for the per-call output cap.
//! A child module (the `provider/anthropic/sse_line_tests.rs` convention)
//! so the private `validate` stays reachable while `models.rs` keeps within
//! its size budget.

use super::*;

/// Parse + validate a one-provider registry (provider name `p`).
fn registry(api: &str, models: &str) -> anyhow::Result<ModelsFile> {
    let creds = r#""baseUrl":"https://o.test","apiKey":"k""#;
    let body = format!(r#""api":"{api}",{creds},"models":{models}"#);
    let raw = format!(r#"{{"providers":{{"p":{{{body}}}}}}}"#);
    let file: ModelsFile = serde_json::from_str(&raw)?;
    validate(&file)?;
    Ok(file)
}

#[test]
fn output_tokens_param_defaults_to_max_tokens() {
    // No declaration (bare id or object without the key) keeps the
    // historical `max_tokens` field — no regression for Kimi / DeepSeek
    // style registries.
    let models = r#"["kimi", {"id":"deepseek","maxOutputTokens":4096}]"#;
    let file = registry("openai-completions", models).unwrap();
    for model in ["kimi", "deepseek"] {
        let resolved = resolve(&file, "p", model).unwrap();
        let param = resolved.model.output_tokens_param;
        assert_eq!(param, OutputTokensParam::MaxTokens);
        assert_eq!(param.field_name(), "max_tokens");
    }
}

#[test]
fn output_tokens_param_parses_reasoning_model_declaration() {
    let models = r#"[{"id":"o3-mini","outputTokensParam":"max_completion_tokens"},
        {"id":"gpt-4o","outputTokensParam":"max_tokens"}]"#;
    let file = registry("openai-completions", models).unwrap();
    let o3 = resolve(&file, "p", "o3-mini").unwrap();
    let param = o3.model.output_tokens_param;
    assert_eq!(param, OutputTokensParam::MaxCompletionTokens);
    assert_eq!(param.field_name(), "max_completion_tokens");
    let gpt = resolve(&file, "p", "gpt-4o").unwrap();
    let param = gpt.model.output_tokens_param;
    assert_eq!(param, OutputTokensParam::MaxTokens);
}

#[test]
fn output_tokens_param_rejects_unknown_values() {
    let models = r#"[{"id":"o3","outputTokensParam":"max_output_tokens"}]"#;
    assert!(registry("openai-completions", models).is_err());
}

#[test]
fn output_tokens_param_is_rejected_on_anthropic_dialect() {
    // Anthropic Messages always sends `max_tokens`; a non-default
    // declaration there would be silently ignored, so loading fails.
    let models = r#"[{"id":"claude","outputTokensParam":"max_completion_tokens"}]"#;
    let error = registry("anthropic-messages", models).unwrap_err();
    assert!(error.to_string().contains("outputTokensParam"), "{error}");
}
