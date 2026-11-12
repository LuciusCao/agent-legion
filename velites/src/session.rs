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
