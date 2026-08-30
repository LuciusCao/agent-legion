//! Session mirror (`session.jsonl`), written under `--session-dir`.
//!
//! Format: one JSON-serialized [`Message`] per line (NDJSON), appended in
//! conversation order as messages are produced. It is an append-only mirror
//! of the in-memory message history — material for a future resume feature.
//! There is intentionally NO resume entry point in M1 (design §3/§11 已决项).

use std::fs::OpenOptions;
use std::io::{self, Write};
use std::path::Path;

use crate::events::Message;

pub struct SessionLog {
    file: std::fs::File,
}

impl SessionLog {
    /// Create (or append to) `<dir>/session.jsonl`, creating `dir` if needed.
    pub fn open(dir: &Path) -> io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("session.jsonl"))?;
        Ok(Self { file })
    }

    pub fn append(&mut self, message: &Message) -> io::Result<()> {
        let line = serde_json::to_string(message)
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
        self.file.write_all(line.as_bytes())?;
        self.file.write_all(b"\n")?;
        self.file.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{ContentBlock, Role, StopReason, Usage};

    #[test]
    fn open_creates_missing_directories_and_session_file() {
        let dir = tempfile::tempdir().unwrap();
        let session_dir = dir.path().join("nested/session");
        // The temporary is dropped at the end of the statement, closing the
        // file before the assertions read the filesystem.
        SessionLog::open(&session_dir).unwrap();
        assert!(session_dir.is_dir());
        assert!(session_dir.join("session.jsonl").is_file());
    }

    #[test]
    fn open_fails_when_the_target_dir_is_a_regular_file() {
        let dir = tempfile::tempdir().unwrap();
        let blocker = dir.path().join("blocker");
        std::fs::write(&blocker, "not a directory").unwrap();
        assert!(SessionLog::open(&blocker).is_err());
    }

    #[test]
    fn append_writes_one_ndjson_line_per_message_in_order() {
        let dir = tempfile::tempdir().unwrap();
        let mut log = SessionLog::open(dir.path()).unwrap();
        log.append(&Message::user("first".into())).unwrap();
        log.append(&Message::user("second".into())).unwrap();
        drop(log);

        let raw = std::fs::read_to_string(dir.path().join("session.jsonl")).unwrap();
        // Line-oriented: every record ends with a newline so later appends
        // (and readers) never see a fused line.
        assert!(raw.ends_with('\n'));
        let messages: Vec<Message> = raw
            .lines()
            .map(|line| serde_json::from_str(line).expect("session lines must be NDJSON"))
            .collect();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0], Message::user("first".into()));
        assert_eq!(messages[1], Message::user("second".into()));
    }

    #[test]
    fn append_round_trips_assistant_metadata() {
        let mut message = Message::bare(
            Role::Assistant,
            vec![
                ContentBlock::Thinking {
                    thinking: "hmm".into(),
                },
                ContentBlock::ToolCall {
                    id: "call-0-0".into(),
                    name: "read".into(),
                    arguments: serde_json::json!({"path": "prompt.md"}),
                },
            ],
        );
        message.usage = Some(Usage {
            input: 10,
            output: 5,
            cache_read: 2,
        });
        message.provider = Some("stub".into());
        message.model = Some("stub".into());
        message.stop_reason = Some(StopReason::ToolUse);

        let dir = tempfile::tempdir().unwrap();
        let mut log = SessionLog::open(dir.path()).unwrap();
        log.append(&message).unwrap();
        drop(log);

        let line = std::fs::read_to_string(dir.path().join("session.jsonl")).unwrap();
        let value: serde_json::Value = serde_json::from_str(line.trim_end()).unwrap();
        // Wire names stay pi-compatible inside the mirror.
        assert_eq!(value["role"], "assistant");
        assert_eq!(value["stopReason"], "toolUse");
        assert_eq!(value["usage"]["cacheRead"], 2);
        let decoded: Message = serde_json::from_value(value).unwrap();
        assert_eq!(decoded, message);
    }

    #[test]
    fn append_round_trips_tool_result_metadata() {
        let message = Message::tool_result(
            "call-0-0".into(),
            "write".into(),
            vec![ContentBlock::Text {
                text: "wrote 7 bytes".into(),
            }],
            true,
        );

        let dir = tempfile::tempdir().unwrap();
        let mut log = SessionLog::open(dir.path()).unwrap();
        log.append(&message).unwrap();
        drop(log);

        let line = std::fs::read_to_string(dir.path().join("session.jsonl")).unwrap();
        let value: serde_json::Value = serde_json::from_str(line.trim_end()).unwrap();
        assert_eq!(value["role"], "toolResult");
        assert_eq!(value["toolCallId"], "call-0-0");
        assert_eq!(value["toolName"], "write");
        assert_eq!(value["isError"], true);
        let decoded: Message = serde_json::from_value(value).unwrap();
        assert_eq!(decoded, message);
    }

    #[test]
    fn open_again_appends_instead_of_truncating() {
        // Append-only mirror: re-opening a session dir must keep earlier
        // records (material for a future resume feature, design §3).
        let dir = tempfile::tempdir().unwrap();
        {
            let mut log = SessionLog::open(dir.path()).unwrap();
            log.append(&Message::user("first session".into())).unwrap();
        }
        {
            let mut log = SessionLog::open(dir.path()).unwrap();
            log.append(&Message::user("second session".into())).unwrap();
        }
        let raw = std::fs::read_to_string(dir.path().join("session.jsonl")).unwrap();
        let messages: Vec<Message> = raw
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0], Message::user("first session".into()));
        assert_eq!(messages[1], Message::user("second session".into()));
    }
}
