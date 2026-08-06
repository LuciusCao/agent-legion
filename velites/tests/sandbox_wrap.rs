//! `velites sandbox wrap` integration tests (design §7 phase 2, EXEC-CODE-003).
//!
//! macOS seatbelt: a wrapped command may write its `--cwd` and nothing else;
//! reads outside the allowlist fail with EPERM; the subcommand dispatch
//! rejects unknown forms. The Linux bubblewrap policy is pinned by argv-level
//! unit tests in src/sandbox.rs; Python-side integration tests probe bwrap
//! availability and skip when it is absent (CI Linux lanes without bwrap).

#[cfg(target_os = "macos")]
use std::path::Path;
use std::process::Command;

#[cfg(target_os = "macos")]
fn run_wrap(cwd: &Path, args: &[String]) -> std::process::Output {
    let mut full: Vec<String> = vec!["sandbox".into(), "wrap".into()];
    full.extend(args.iter().cloned());
    Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(&full)
        .current_dir(cwd)
        .output()
        .expect("failed to spawn velites")
}

#[cfg(target_os = "macos")]
fn wrap_args(cwd: &Path, command: &str) -> Vec<String> {
    vec![
        "--cwd".into(),
        cwd.to_string_lossy().into_owned(),
        "--".into(),
        "/bin/sh".into(),
        "-c".into(),
        command.into(),
    ]
}

#[cfg(target_os = "macos")]
fn outside_dir(tag: &str) -> std::path::PathBuf {
    // A directory outside $TMPDIR: wrap mode whitelists the system temp root
    // read-write, so an "outside" location must live under $HOME (which the
    // policy never grants).
    let home = std::env::var_os("HOME").expect("HOME must be set");
    let dir = Path::new(&home).join(format!(".velites-wrap-test-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[cfg(target_os = "macos")]
#[test]
fn wrap_allows_cwd_write_and_denies_outside_writes_and_reads() {
    let job = tempfile::tempdir().unwrap();
    let outside = outside_dir("escape");
    let secret = outside.join("secret.txt");
    std::fs::write(&secret, "top secret").unwrap();

    // Write inside --cwd succeeds; the file really lands on the host fs.
    let output = run_wrap(
        job.path(),
        &wrap_args(job.path(), "echo hi > out.txt && cat out.txt"),
    );
    assert!(output.status.success(), "cwd write failed: {output:?}");
    assert_eq!(
        std::fs::read_to_string(job.path().join("out.txt")).unwrap(),
        "hi\n"
    );

    // Writes outside --cwd are denied with EPERM.
    let output = run_wrap(
        job.path(),
        &wrap_args(
            job.path(),
            &format!("echo nope > {}/escape.txt", outside.display()),
        ),
    );
    assert!(
        !output.status.success(),
        "outside write must fail: {output:?}"
    );
    assert!(!outside.join("escape.txt").exists());

    // Reads outside the allowlist are denied with EPERM.
    let output = run_wrap(
        job.path(),
        &wrap_args(job.path(), &format!("cat {}", secret.display())),
    );
    assert!(
        !output.status.success(),
        "outside read must fail: {output:?}"
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Operation not permitted"),
        "expected EPERM on stderr: {output:?}"
    );
    std::fs::remove_dir_all(&outside).unwrap();
}

#[cfg(target_os = "macos")]
#[test]
fn wrap_allow_read_grants_read_only_access() {
    let job = tempfile::tempdir().unwrap();
    let shared = outside_dir("shared");
    std::fs::write(shared.join("data.txt"), "shared").unwrap();
    let args = vec![
        "--cwd".into(),
        job.path().to_string_lossy().into_owned(),
        "--allow-read".into(),
        shared.to_string_lossy().into_owned(),
        "--".into(),
        "/bin/sh".into(),
        "-c".into(),
        format!(
            "cat {} && echo nope > {}/blocked.txt",
            shared.join("data.txt").display(),
            shared.display()
        ),
    ];
    let output = run_wrap(job.path(), &args);
    // The read is allowed, the write to the read-only root is not.
    assert!(!output.status.success(), "read-only root write must fail");
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("shared"),
        "stdout missing shared: {output:?}"
    );
    assert!(!shared.join("blocked.txt").exists());
    std::fs::remove_dir_all(&shared).unwrap();
}

#[test]
fn sandbox_subcommand_rejects_unknown_forms() {
    let dir = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["sandbox", "explode"])
        .current_dir(dir.path())
        .output()
        .expect("failed to spawn velites");
    assert_eq!(output.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("sandbox wrap"),
        "usage hint missing: {output:?}"
    );
}
