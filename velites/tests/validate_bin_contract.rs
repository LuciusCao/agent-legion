//! `validate` subcommand contract tests (issue #443, contract location
//! migrated in #542), in the style of tests/sandbox_bin_contract.rs: both
//! binaries (`velites validate` and `velites-sandbox validate`) expose the
//! same standalone output-contract check with identical observable
//! behavior:
//!
//! - contract declared and all rules hold → stdout `mode=contract`, exit 0;
//! - no skill dir declares a contract → stdout `mode=existence`,
//!   exit 0 (the Host falls back to its legacy python check on this signal);
//! - violations → one per stderr line, exit 1;
//! - contract parse error / bad args / IO error → stderr, exit 2.
//!
//! #542 adds the source tier: the skill-root `contract.yaml` is the
//! normative location; the embedded ```yaml contract block in
//! `references/output-contract.md` is deprecated but still honored and
//! then reports itself on stdout
//! (`source=embedded-block (deprecated; migrate to contract.yaml)`) — an
//! extra stdout line only, never a changed exit code or stderr shape.

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

/// Skill dir whose ROOT contract.yaml demands a `script.md` text artifact.
fn contract_skill(dir: &Path) -> std::path::PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: script.md\n    format: text\n    min_chars: 5\n",
    )
    .unwrap();
    skill
}

/// Same contract, embedded in the deprecated references/output-contract.md.
fn embedded_contract_skill(dir: &Path) -> std::path::PathBuf {
    let skill = dir.join("skill");
    std::fs::create_dir(&skill).unwrap();
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
fn no_contract_prints_mode_existence_and_exits_zero() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();

    for bin in bins() {
        // No --skill at all, and a skill dir without a contract, both degrade.
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
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(skill.join("contract.yaml"), "files: []\n").unwrap();

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

// --- #542: root contract.yaml tiers and the deprecation signal ---

#[test]
fn embedded_block_still_validates_and_prints_the_deprecation_signal() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "long enough").unwrap();
    let skill = embedded_contract_skill(dir.path());

    for bin in bins() {
        // Pass: mode line + the migration signal on stdout, exit 0.
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(0), "{bin:?}: {:?}", out);
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("mode=contract"), "{stdout}");
        assert!(
            stdout.contains("source=embedded-block (deprecated; migrate to contract.yaml)"),
            "{stdout}"
        );
    }
}

#[test]
fn embedded_block_violation_exit_code_is_unchanged() {
    // The deprecation signal must not touch the exit-code semantics: a
    // violation is still exit 1 with the violations on stderr (the signal
    // rides stdout on BOTH paths — the Host reads exit code + stderr only,
    // so the extra stdout line stays inert for every consumer).
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let skill = embedded_contract_skill(dir.path());

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(1), "{bin:?}: {:?}", out);
        assert_eq!(
            String::from_utf8_lossy(&out.stderr).trim(),
            "script.md: missing required file"
        );
        assert!(
            String::from_utf8_lossy(&out.stdout).contains("source=embedded-block"),
            "{bin:?}: {:?}",
            out
        );
    }
}

#[test]
fn root_contract_yaml_wins_over_the_embedded_block() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "long enough").unwrap();
    let skill = dir.path().join("skill");
    std::fs::create_dir(&skill).unwrap();
    std::fs::write(
        skill.join("contract.yaml"),
        "files:\n  - path: script.md\n    format: text\n    min_chars: 100\n",
    )
    .unwrap();
    std::fs::create_dir_all(skill.join("references")).unwrap();
    std::fs::write(
        skill.join("references/output-contract.md"),
        "```yaml contract\nfiles:\n  - path: script.md\n    format: text\n```\n",
    )
    .unwrap();

    for bin in bins() {
        // The root's stricter min_chars wins: violation, exit 1, and no
        // deprecation signal (the root is the normative source).
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(1), "{bin:?}: {:?}", out);
        assert!(
            String::from_utf8_lossy(&out.stderr).contains("too short"),
            "{bin:?}"
        );
        assert!(out.stdout.is_empty());
    }
}

#[test]
fn malformed_root_contract_fails_closed_even_with_a_valid_embedded_block() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(job.join("script.md"), "long enough").unwrap();
    let skill = embedded_contract_skill(dir.path());
    std::fs::write(skill.join("contract.yaml"), "files: [\n").unwrap();

    for bin in bins() {
        let out = run_validate(&bin, &job, &[&skill]);
        assert_eq!(out.status.code(), Some(2), "{bin:?}: {:?}", out);
        assert!(String::from_utf8_lossy(&out.stderr).contains("contract parse error:"));
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
