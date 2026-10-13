//! Per-model output-cap field dialect for `openai-completions` providers
//! (#1093). A child module of `models` (re-exported as
//! `models::OutputTokensParam`) so the registry file keeps within its size
//! budget.

use serde::Deserialize;

/// Which request field an OpenAI-compatible model accepts for the per-call
/// output cap. Most compatible services (Kimi, DeepSeek, …) take
/// `max_tokens`; OpenAI reasoning models (o-series) reject it and require
/// `max_completion_tokens`. The default keeps the historical wire shape.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
pub enum OutputTokensParam {
    #[default]
    #[serde(rename = "max_tokens")]
    MaxTokens,
    #[serde(rename = "max_completion_tokens")]
    MaxCompletionTokens,
}

impl OutputTokensParam {
    /// The JSON key written into the chat-completions request body.
    pub fn field_name(self) -> &'static str {
        match self {
            Self::MaxTokens => "max_tokens",
            Self::MaxCompletionTokens => "max_completion_tokens",
        }
    }
}
