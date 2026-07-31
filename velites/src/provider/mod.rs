//! Provider layer. M1 ships only the fixture-driven `stub`; the
//! OpenAI-compatible SSE provider (`gateway`) lands in M2 and must implement
//! the same [`Provider`] trait.
//!
//! The trait resolves to ONE final assistant message per call: any streaming
//! happens inside the provider and never escapes as delta events (there are
//! no delta events in velites by design).

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
    #[error("provider call failed: {0}")]
    Call(String),
    #[error("stub fixture error: {0}")]
    Fixture(String),
}

/// A model backend. M2 adds `openai_compat` next to `stub`.
///
/// `async fn in trait` is fine here: the trait is crate-internal (both impls
/// live in this crate) and implementors are all `Send + Sync`.
#[allow(async_fn_in_trait)]
pub trait Provider: Send + Sync {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError>;
}
