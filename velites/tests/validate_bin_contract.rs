//! `validate` subcommand contract tests (issue #443), in the style of
//! tests/sandbox_bin_contract.rs: both binaries (`velites validate` and
//! `velites-sandbox validate`) expose the same standalone output-contract
//! check with identical observable behavior:
//!
//! - contract declared and all rules hold → stdout `mode=contract`, exit 0;
//! - no skill dir declares a contract block → stdout `mode=existence`,
//!   exit 0 (the Host falls back to its legacy python check on this signal);
//! - violations → one per stderr line, exit 1;
//! - contract parse error / bad args / IO error → stderr, exit 2.

use std::path::Path;
use std::process::Command;

fn velites_bin() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_velites"))
}

fn velites_sandbox_bin() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_velites-sandbox"))
}

fn run_validate(binary: &Path, job: &Path, skills: &[&Path]) -> std::process::Output {
    let mut cmd = Command::new(binary);
    cmd.arg("validate")
        .arg("--job-dir")
        .arg(job)
        .current_dir(std::env::temp_dir());
    for skill in skills {
        cmd.arg("--skill").arg(skill);
    }
    cmd.output()
        .unwrap_or_else(|e| panic!("spawn {binary:?}: {e}"))
}

fn bins() -> [std::path::PathBuf; 2] {
    [velites_bin(), velites_sandbox_bin()]
}

/// Skill dir whose contract demands a `script.md` text artifact.
fn contract_skill(dir: &Path) -> std::path::PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "```yaml contract\nfiles:\n  - path: script.md\n    format: text\n    min_chars: 5\n```\n",
    )
    .unwrap();
    skill
}

#[test]
fn contract_ok_prints_mode_contract_and_exits_zero() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "long enough").unwrap();
    let skill = contract_skill(dir.path());

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(0), "{bin:?}: {:?}", out);
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "mode=contract");
    }
}

#[test]
fn no_contract_block_prints_mode_existence_and_exits_zero() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();

    for bin in bins() {
        // No --skill at all, and a skill dir without a block, both degrade.
        for skills in [&[][..], &[skill.as_path()][..]] {
            let out = run_validate(&bin, &job, skills);
            assert_eq!(out.status.code(), Some(0), "{bin:?}: {:?}", out);
            assert_eq!(
                String::from_utf8_lossy(&out.stdout).trim(),
                "mode=existence"
            );
        }
    }
}

#[test]
fn violations_go_to_stderr_and_exit_one() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let skill = contract_skill(dir.path());

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(1), "{bin:?}: {:?}", out);
        assert_eq!(
            String::from_utf8_lossy(&out.stderr).trim(),
            "script.md: missing required file"
        );
        assert!(out.stdout.is_empty());
    }
}

#[test]
fn contract_parse_error_exits_two() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "```yaml contract\nfiles: []\n```\n",
    )
    .unwrap();

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(2), "{bin:?}: {:?}", out);
        assert!(String::from_utf8_lossy(&out.stderr).contains("contract parse error:"));
    }
    // A nonexistent job dir is an I/O error: also exit 2.
    for bin in bins() {
        let out = run_validate(&bin, &dir.path().join("missing"), &[&skill]);
        assert_eq!(out.status.code(), Some(2), "{bin:?}: {:?}", out);
    }
}

#[test]
fn first_skill_dir_with_a_contract_wins() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "long enough").unwrap();
    let plain = dir.path().join("plain");
    std::fs::create_dir(&plain).unwrap();
    let skill = contract_skill(dir.path());

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&plain, &skill]);
        assert_eq!(out.status.code(), Some(0), "{bin:?}: {:?}", out);
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "mode=contract");
    }
}

#[test]
fn missing_job_dir_arg_is_a_cli_error() {
    let job = tempfile::tempdir().unwrap();
    for bin in bins() {
        let out = Command::new(&bin)
            .arg("validate")
            .current_dir(job.path())
            .output()
            .expect("spawn validate");
        assert_eq!(out.status.code(), Some(2), "{bin:?}: {:?}", out);
    }
}
