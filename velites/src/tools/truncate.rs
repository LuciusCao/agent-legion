//! Tool output truncation, aligned with pi's `truncate.js` semantics.
//!
//! Two independent limits — whichever is hit first wins:
//! - line limit ([`DEFAULT_MAX_LINES`], 2000)
//! - byte limit ([`DEFAULT_MAX_BYTES`], 50KB = 50 * 1024)
//!
//! `truncate_head` keeps the beginning (file reads), `truncate_tail` keeps
//! the end (bash output, where errors and final results live). Neither
//! splits a line, except the tail edge case where the single last line
//! alone exceeds the byte limit: then the tail of that line is kept.

pub const DEFAULT_MAX_LINES: usize = 2000;
pub const DEFAULT_MAX_BYTES: usize = 50 * 1024;
/// Human-readable form of [`DEFAULT_MAX_BYTES`] used in notices.
pub const MAX_BYTES_DISPLAY: &str = "50KB";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TruncatedBy {
    Lines,
    Bytes,
}

#[derive(Debug)]
pub struct Truncation {
    pub content: String,
    pub truncated: bool,
    pub truncated_by: Option<TruncatedBy>,
    pub total_lines: usize,
    pub total_bytes: usize,
    pub output_lines: usize,
    pub output_bytes: usize,
    /// Tail truncation only: the single retained line is a partial tail of
    /// the original last line (that line alone exceeds the byte limit).
    pub last_line_partial: bool,
    /// Head truncation only: the first line alone exceeds the byte limit,
    /// so nothing can be shown.
    pub first_line_exceeds_limit: bool,
}

impl Truncation {
    fn untruncated(content: &str, total_lines: usize) -> Self {
        Self {
            content: content.to_string(),
            truncated: false,
            truncated_by: None,
            total_lines,
            total_bytes: content.len(),
            output_lines: total_lines,
            output_bytes: content.len(),
            last_line_partial: false,
            first_line_exceeds_limit: false,
        }
    }
}

/// Format bytes as a human-readable size (pi `formatSize`).
pub fn format_size(bytes: usize) -> String {
    if bytes < 1024 {
        format!("{bytes}B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1}KB", bytes as f64 / 1024.0)
    } else {
        format!("{:.1}MB", bytes as f64 / (1024.0 * 1024.0))
    }
}

/// Split like pi's `splitLinesForCounting`: on `\n`, with the empty tail
/// after a trailing newline dropped; empty content has zero lines.
fn split_lines(content: &str) -> Vec<&str> {
    if content.is_empty() {
        return Vec::new();
    }
    let mut lines: Vec<&str> = content.split('\n').collect();
    if content.ends_with('\n') {
        lines.pop();
    }
    lines
}

/// Truncate from the head, keeping the first lines (file-read semantics).
pub fn truncate_head(content: &str) -> Truncation {
    let total_bytes = content.len();
    let lines = split_lines(content);
    let total_lines = lines.len();
    if total_lines <= DEFAULT_MAX_LINES && total_bytes <= DEFAULT_MAX_BYTES {
        return Truncation::untruncated(content, total_lines);
    }
    if lines[0].len() > DEFAULT_MAX_BYTES {
        return Truncation {
            content: String::new(),
            truncated: true,
            truncated_by: Some(TruncatedBy::Bytes),
            total_lines,
            total_bytes,
            output_lines: 0,
            output_bytes: 0,
            last_line_partial: false,
            first_line_exceeds_limit: true,
        };
    }
    let mut kept: Vec<&str> = Vec::new();
    let mut kept_bytes = 0usize;
    let mut truncated_by = TruncatedBy::Lines;
    for (index, line) in lines.iter().enumerate().take(DEFAULT_MAX_LINES) {
        // +1 for the newline separator between kept lines (same as pi).
        let line_bytes = line.len() + usize::from(index > 0);
        if kept_bytes + line_bytes > DEFAULT_MAX_BYTES {
            truncated_by = TruncatedBy::Bytes;
            break;
        }
        kept.push(line);
        kept_bytes += line_bytes;
    }
    if kept.len() >= DEFAULT_MAX_LINES && kept_bytes <= DEFAULT_MAX_BYTES {
        truncated_by = TruncatedBy::Lines;
    }
    let content = kept.join("\n");
    Truncation {
        output_lines: kept.len(),
        output_bytes: content.len(),
        content,
        truncated: true,
        truncated_by: Some(truncated_by),
        total_lines,
        total_bytes,
        last_line_partial: false,
        first_line_exceeds_limit: false,
    }
}

/// Truncate from the tail, keeping the last lines (bash-output semantics:
/// errors and results live at the end).
pub fn truncate_tail(content: &str) -> Truncation {
    let total_bytes = content.len();
    let lines = split_lines(content);
    let total_lines = lines.len();
    if total_lines <= DEFAULT_MAX_LINES && total_bytes <= DEFAULT_MAX_BYTES {
        return Truncation::untruncated(content, total_lines);
    }
    let mut kept: Vec<&str> = Vec::new(); // built back-to-front
    let mut partial: Option<String> = None;
    let mut kept_bytes = 0usize;
    let mut truncated_by = TruncatedBy::Lines;
    let mut last_line_partial = false;
    for line in lines.iter().rev().take(DEFAULT_MAX_LINES) {
        // +1 for the newline separator; the last line of the output has none.
        let line_bytes = line.len() + usize::from(!kept.is_empty());
        if kept_bytes + line_bytes > DEFAULT_MAX_BYTES {
            truncated_by = TruncatedBy::Bytes;
            if kept.is_empty() {
                // Edge case: the last line alone exceeds the byte limit —
                // keep the tail of the line (pi's only partial-line case).
                let tail = tail_within_bytes(line, DEFAULT_MAX_BYTES);
                kept_bytes = tail.len();
                partial = Some(tail.to_string());
                last_line_partial = true;
            }
            break;
        }
        kept.push(line);
        kept_bytes += line_bytes;
    }
    if kept.len() >= DEFAULT_MAX_LINES && kept_bytes <= DEFAULT_MAX_BYTES {
        truncated_by = TruncatedBy::Lines;
    }
    kept.reverse();
    let content = match partial {
        Some(tail) => tail,
        None => kept.join("\n"),
    };
    let output_lines = kept.len() + usize::from(last_line_partial);
    Truncation {
        output_bytes: content.len(),
        output_lines,
        content,
        truncated: true,
        truncated_by: Some(truncated_by),
        total_lines,
        total_bytes,
        last_line_partial,
        first_line_exceeds_limit: false,
    }
}

/// Keep the last `max_bytes` of `line`, not splitting a multi-byte UTF-8
/// character (pi `truncateStringToBytesFromEnd`).
fn tail_within_bytes(line: &str, max_bytes: usize) -> &str {
    if line.len() <= max_bytes {
        return line;
    }
    let mut start = line.len() - max_bytes;
    while !line.is_char_boundary(start) {
        start += 1;
    }
    &line[start..]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines_of(n: usize) -> String {
        (1..=n)
            .map(|i| format!("line {i}"))
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn no_truncation_when_under_both_limits() {
        let content = lines_of(DEFAULT_MAX_LINES);
        let result = truncate_head(&content);
        assert!(!result.truncated);
        assert_eq!(result.content, content);
        assert_eq!(result.output_lines, DEFAULT_MAX_LINES);

        let result = truncate_tail(&content);
        assert!(!result.truncated);
        assert_eq!(result.content, content);
    }

    #[test]
    fn empty_output_is_not_truncated() {
        for result in [truncate_head(""), truncate_tail("")] {
            assert!(!result.truncated);
            assert_eq!(result.total_lines, 0);
            assert_eq!(result.content, "");
        }
    }

    #[test]
    fn head_truncates_by_lines_keeping_first_lines() {
        let content = lines_of(DEFAULT_MAX_LINES + 500);
        let result = truncate_head(&content);
        assert!(result.truncated);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Lines));
        assert_eq!(result.output_lines, DEFAULT_MAX_LINES);
        assert_eq!(result.total_lines, DEFAULT_MAX_LINES + 500);
        assert!(result.content.starts_with("line 1\n"));
        assert!(result.content.ends_with("line 2000"));
        assert!(!result.content.contains("line 2001"));
    }

    #[test]
    fn head_truncates_by_bytes_without_splitting_a_line() {
        // 600 lines × 100 bytes ≈ 60KB > 50KB, well under 2000 lines.
        let line = "a".repeat(100);
        let content = (0..600)
            .map(|_| line.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let result = truncate_head(&content);
        assert!(result.truncated);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));
        assert!(result.output_bytes <= DEFAULT_MAX_BYTES);
        assert!(result.output_lines < 600);
        // Every kept line is complete.
        assert!(result.content.lines().all(|l| l.len() == 100));
    }

    #[test]
    fn head_first_line_exceeds_limit() {
        let content = format!("{}\nshort", "x".repeat(DEFAULT_MAX_BYTES + 1));
        let result = truncate_head(&content);
        assert!(result.truncated);
        assert!(result.first_line_exceeds_limit);
        assert_eq!(result.output_lines, 0);
        assert_eq!(result.content, "");
    }

    #[test]
    fn tail_truncates_by_lines_keeping_last_lines() {
        let content = lines_of(DEFAULT_MAX_LINES + 500);
        let result = truncate_tail(&content);
        assert!(result.truncated);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Lines));
        assert_eq!(result.output_lines, DEFAULT_MAX_LINES);
        assert!(result.content.starts_with("line 501\n"));
        assert!(result.content.ends_with("line 2500"));
        assert!(!result.content.contains("line 500\n"));
        assert!(!result.last_line_partial);
    }

    #[test]
    fn tail_truncates_by_bytes_without_splitting_a_line() {
        let line = "b".repeat(100);
        let content = (0..600)
            .map(|_| line.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let result = truncate_tail(&content);
        assert!(result.truncated);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));
        assert!(result.output_bytes <= DEFAULT_MAX_BYTES);
        assert!(!result.last_line_partial);
        assert!(result.content.lines().all(|l| l.len() == 100));
        // The kept suffix is the end of the original content.
        assert!(content.ends_with(&result.content));
    }

    #[test]
    fn tail_last_line_alone_exceeds_limit_keeps_partial_tail() {
        let content = format!("first\n{}", "y".repeat(DEFAULT_MAX_BYTES + 100));
        let result = truncate_tail(&content);
        assert!(result.truncated);
        assert!(result.last_line_partial);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));
        assert_eq!(result.output_bytes, DEFAULT_MAX_BYTES);
        assert!(!result.content.contains("first"));
    }

    #[test]
    fn tail_partial_tail_respects_utf8_boundaries() {
        // 中 is 3 bytes; a byte cut must not land mid-character.
        let long = "中".repeat(DEFAULT_MAX_BYTES); // 3 × 50KB bytes, one line
        let content = format!("header\n{long}");
        let result = truncate_tail(&content);
        assert!(result.last_line_partial);
        assert!(result.output_bytes <= DEFAULT_MAX_BYTES);
        assert!(result.content.chars().all(|c| c == '中'));
        // Dropped at most one char to reach a char boundary.
        assert!(result.output_bytes >= DEFAULT_MAX_BYTES - 3);
    }

    #[test]
    fn head_byte_count_treats_multibyte_lines_as_bytes() {
        // 1700 lines × 30 × 中 (3 bytes) = 153KB > 50KB, under 2000 lines.
        let line = "中".repeat(30);
        let content = (0..1700)
            .map(|_| line.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let result = truncate_head(&content);
        assert!(result.truncated);
        assert_eq!(result.truncated_by, Some(TruncatedBy::Bytes));
        assert!(result.output_bytes <= DEFAULT_MAX_BYTES);
        assert!(result.content.chars().all(|c| c == '中' || c == '\n'));
    }

    #[test]
    fn just_over_line_limit_truncates() {
        let content = lines_of(DEFAULT_MAX_LINES + 1);
        assert!(truncate_head(&content).truncated);
        assert!(truncate_tail(&content).truncated);
    }

    #[test]
    fn just_over_byte_limit_truncates() {
        let content = "z".repeat(DEFAULT_MAX_BYTES + 1) + "\n";
        let result = truncate_head(&content);
        assert!(result.truncated);
        assert!(result.first_line_exceeds_limit);
    }

    #[test]
    fn format_size_matches_pi_shape() {
        assert_eq!(format_size(512), "512B");
        assert_eq!(format_size(1536), "1.5KB");
        assert_eq!(format_size(5 * 1024 * 1024), "5.0MB");
    }
}
