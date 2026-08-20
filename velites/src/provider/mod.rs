//! Provider layer. Two backends ship behind the same [`Provider`] trait: the
//! fixture-driven `stub` (M1, deterministic tests) and `openai_compat` (M2,
//! OpenAI chat completions over SSE — used for `gateway`).
//!
//! The trait resolves to ONE final assistant message per call: any streaming
//! happens inside the provider and never escapes as delta events (there are
//! no delta events in velites by design).

pub mod anthropic;
pub mod openai_compat;
pub mod retry;
pub mod stub;

use serde_json::Value;

use crate::events::Message;

/// Static tool description handed to the provider.
#[derive(Debug, Clone)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    /// JSON Schema for the tool arguments.
    pub parameters: Value,
}

/// One completion request: the full conversation plus static context.
pub struct CompletionRequest<'a> {
    pub model: &'a str,
    pub system: &'a str,
    pub messages: &'a [Message],
    pub tools: &'a [ToolSpec],
    pub thinking: Option<&'a str>,
}

#[derive(Debug, thiserror::Error)]
pub enum ProviderError {
    /// Deterministic failure — retrying the identical request cannot help
    /// (4xx other than 429, malformed responses, non-retryable stream
    /// errors). Surfaces directly as `stopReason=error`.
    #[error("provider call failed: {0}")]
    Call(String),
    /// Transient failure — worth retrying with backoff (network errors,
    /// timeouts, 429, 5xx, a stream that ended without a finish reason).
    #[error("provider call failed (transient): {0}")]
    Transient(String),
    #[error("stub fixture error: {0}")]
    Fixture(String),
}

impl ProviderError {
    /// Whether [`retry::RetryProvider`] should attempt the call again.
    pub fn is_retryable(&self) -> bool {
        matches!(self, Self::Transient(_))
    }
}

/// A model backend. M2 adds `openai_compat` next to `stub`.
///
/// `async fn in trait` is fine here: the trait is crate-internal (both impls
/// live in this crate) and implementors are all `Send + Sync`.
#[allow(async_fn_in_trait)]
pub trait Provider: Send + Sync {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError>;
}
