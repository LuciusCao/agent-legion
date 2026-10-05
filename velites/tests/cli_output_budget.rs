//! `--max-output-tokens` CLI surface (#952): the per-call output cap parses
//! as a positive integer; zero is rejected (every call would fail).

use clap::Parser;
use velites::cli::Cli;

fn parse(value: &str) -> Result<Cli, clap::Error> {
    Cli::try_parse_from(["velites", "--provider", "stub", "--max-output-tokens", value, "work"])
}

#[test]
fn max_output_tokens_parses_positive_values() {
    assert_eq!(parse("32000").unwrap().max_output_tokens, Some(32000));
}

#[test]
fn max_output_tokens_rejects_zero() {
    let err = parse("0").unwrap_err();
    assert_eq!(err.kind(), clap::error::ErrorKind::ValueValidation);
}
