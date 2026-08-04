//! Stub provider: replays a scripted response sequence from a JSON fixture,
//! giving deterministic, LLM-free execution for tests and golden runs.
//!
//! Fixture format:
//!
//! ```json
//! {
//!   "responses": [
//!     {
//!       "content": [
//!         {"type": "text", "text": "Let me look."},
//!         {"type": "toolCall", "name": "read", "arguments": {"path": "in.txt"}}
//!       ],
//!       "usage": {"input": 10, "output": 5, "cacheRead": 0}
//!     },
//!     {
//!       "content": [{"type": "text", "text": "Done."}],
//!       "stopReason": "stop",
//!       "usage": {"input": 20, "output": 8, "cacheRead": 0}
//!     }
//!   ]
//! }
//! ```
//!
//! Each `complete()` call pops the next response. `toolCall` blocks may omit
//! `id` (auto-filled as `call-<n>-<i>`); `stopReason` defaults to `toolUse`
//! when the response contains tool calls, otherwise `stop`.

use std::collections::VecDeque;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

use serde::Deserialize;

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::{ContentBlock, Message, Role, StopReason, Usage};

#[derive(Debug, Deserialize)]
struct StubFixture {
    responses: Vec<StubResponse>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StubResponse {
    content: Vec<ContentBlock>,
    #[serde(default)]
    usage: Option<Usage>,
    #[serde(default)]
    stop_reason: Option<StopReason>,
    #[serde(default)]
    error_message: Option<String>,
}

pub struct StubProvider {
    responses: Mutex<VecDeque<StubResponse>>,
    call_count: AtomicUsize,
}

impl StubProvider {
    pub fn from_fixture(path: &Path) -> anyhow::Result<Self> {
        let raw = std::fs::read_to_string(path)?;
        let fixture: StubFixture = serde_json::from_str(&raw)?;
        if fixture.responses.is_empty() {
            anyhow::bail!("stub fixture {} has no responses", path.display());
        }
        Ok(Self {
            responses: Mutex::new(fixture.responses.into()),
            call_count: AtomicUsize::new(0),
        })
    }
}

impl Provider for StubProvider {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        let mut response = {
            let mut queue = self
                .responses
                .lock()
                .map_err(|_| ProviderError::Fixture("response queue poisoned".into()))?;
            queue
                .pop_front()
                .ok_or_else(|| ProviderError::Fixture("stub fixture exhausted".into()))?
        };
        let call_index = self.call_count.fetch_add(1, Ordering::SeqCst);

        let mut has_tool_call = false;
        for (block_index, block) in response.content.iter_mut().enumerate() {
            if let ContentBlock::ToolCall { id, .. } = block {
                has_tool_call = true;
                if id.is_empty() {
                    *id = format!("call-{call_index}-{block_index}");
                }
            }
        }

        let stop_reason = response.stop_reason.unwrap_or(if has_tool_call {
            StopReason::ToolUse
        } else {
            StopReason::Stop
        });

        let mut message = Message::bare(Role::Assistant, response.content);
        message.usage = Some(response.usage.unwrap_or_default());
        message.provider = Some("stub".to_string());
        message.model = Some(req.model.to_string());
        message.stop_reason = Some(stop_reason);
        message.error_message = response.error_message;
        Ok(message)
    }
}
