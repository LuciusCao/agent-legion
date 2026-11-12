use velites::tools::truncate::{
    format_size, truncate_head, truncate_tail, TruncatedBy, DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES,
};

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
