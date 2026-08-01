//! Minimal HTTP/1.1 fixture server for provider integration tests.
//!
//! One request per connection, `Connection: close`, responses popped from a
//! script queue. Supports SSE bodies, JSON bodies, arbitrary status codes,
//! and mid-body truncation (simulating an interrupted stream). Every request
//! line + body is recorded for assertions.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinHandle;

#[derive(Debug, Clone)]
pub struct RecordedRequest {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl RecordedRequest {
    pub fn header(&self, name: &str) -> Option<&str> {
        let name = name.to_ascii_lowercase();
        self.headers
            .iter()
            .find(|(key, _)| key == &name)
            .map(|(_, value)| value.as_str())
    }

    pub fn body_json(&self) -> serde_json::Value {
        serde_json::from_str(&self.body).expect("recorded request body must be JSON")
    }
}

/// One scripted response.
#[derive(Debug, Clone)]
pub struct MockResponse {
    pub status: u16,
    pub content_type: &'static str,
    pub body: String,
    /// Write only the first N bytes of the body, then close the connection
    /// (the advertised Content-Length still covers the full body, so the
    /// client sees a truncated stream).
    pub truncate_at: Option<usize>,
}

impl MockResponse {
    pub fn sse(body: impl Into<String>) -> Self {
        Self {
            status: 200,
            content_type: "text/event-stream",
            body: body.into(),
            truncate_at: None,
        }
    }

    pub fn json(status: u16, body: impl Into<String>) -> Self {
        Self {
            status,
            content_type: "application/json",
            body: body.into(),
            truncate_at: None,
        }
    }

    /// An SSE response whose connection drops after `n` bytes — mid-stream
    /// interruption.
    pub fn truncated_sse(body: impl Into<String>, n: usize) -> Self {
        Self {
            truncate_at: Some(n),
            ..Self::sse(body)
        }
    }
}

pub struct MockServer {
    pub url: String,
    pub requests: Arc<Mutex<Vec<RecordedRequest>>>,
    accept_loop: JoinHandle<()>,
}

impl MockServer {
    pub async fn start(responses: Vec<MockResponse>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let requests: Arc<Mutex<Vec<RecordedRequest>>> = Arc::new(Mutex::new(Vec::new()));
        let responses = Arc::new(Mutex::new(VecDeque::from(responses)));
        let accept_loop = {
            let requests = Arc::clone(&requests);
            let responses = Arc::clone(&responses);
            tokio::spawn(async move {
                loop {
                    let Ok((socket, _)) = listener.accept().await else {
                        return;
                    };
                    let requests = Arc::clone(&requests);
                    let responses = Arc::clone(&responses);
                    tokio::spawn(async move {
                        let _ = handle_connection(socket, requests, responses).await;
                    });
                }
            })
        };
        Self {
            url: format!("http://{addr}"),
            requests,
            accept_loop,
        }
    }

    pub fn recorded(&self) -> Vec<RecordedRequest> {
        self.requests.lock().unwrap().clone()
    }
}

impl Drop for MockServer {
    fn drop(&mut self) {
        self.accept_loop.abort();
    }
}

async fn handle_connection(
    mut socket: TcpStream,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
    responses: Arc<Mutex<VecDeque<MockResponse>>>,
) -> std::io::Result<()> {
    let request = read_request(&mut socket).await?;
    requests.lock().unwrap().push(request);

    let response = responses.lock().unwrap().pop_front().unwrap_or_else(|| {
        MockResponse::json(500, r#"{"error":{"message":"unexpected request"}}"#)
    });

    let reason = match response.status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        503 => "Service Unavailable",
        _ => "Status",
    };
    let body = response.body.as_bytes();
    let header = format!(
        "HTTP/1.1 {} {}\r\ncontent-type: {}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        response.status,
        reason,
        response.content_type,
        body.len(),
    );
    socket.write_all(header.as_bytes()).await?;
    let written = match response.truncate_at {
        Some(n) => &body[..n.min(body.len())],
        None => body,
    };
    socket.write_all(written).await?;
    socket.flush().await?;
    Ok(())
}

async fn read_request(socket: &mut TcpStream) -> std::io::Result<RecordedRequest> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 4096];
    let header_end = loop {
        if let Some(pos) = find_subsequence(&buffer, b"\r\n\r\n") {
            break pos;
        }
        let n = socket.read(&mut chunk).await?;
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "connection closed before headers complete",
            ));
        }
        buffer.extend_from_slice(&chunk[..n]);
    };

    let head = String::from_utf8_lossy(&buffer[..header_end]).into_owned();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or_default();
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_string();
    let path = parts.next().unwrap_or_default().to_string();
    let headers: Vec<(String, String)> = lines
        .filter_map(|line| {
            let (name, value) = line.split_once(':')?;
            Some((name.trim().to_ascii_lowercase(), value.trim().to_string()))
        })
        .collect();
    let content_length: usize = headers
        .iter()
        .find(|(name, _)| name == "content-length")
        .and_then(|(_, value)| value.parse().ok())
        .unwrap_or(0);

    let mut body = buffer[header_end + 4..].to_vec();
    while body.len() < content_length {
        let n = socket.read(&mut chunk).await?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    body.truncate(content_length);

    Ok(RecordedRequest {
        method,
        path,
        headers,
        body: String::from_utf8_lossy(&body).into_owned(),
    })
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}
