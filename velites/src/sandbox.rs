//! OS-level filesystem sandbox for the `bash` tool (design §5, M4.5).
//!
//! The read/write tools already canonicalize paths in-process; this module is
//! the second layer: the bash child (and everything it forks) is wrapped in an
//! OS sandbox so a prompt-level mistake cannot scan or mutate the host.
//!
//! Backends:
//!
//! - macOS: `sandbox-exec` with a seatbelt profile generated at startup
//!   (`deny default`; reads allowed for system paths + cwd/session/skills,
//!   writes only for cwd/session/$TMPDIR//tmp plus /dev).
//! - Linux: `bubblewrap` (`--ro-bind / /` read-only root, tmpfs on /tmp,
//!   read-write binds for cwd/session/$TMPDIR).
//!
//! Fail-closed: [`Sandbox::new`] probes the backend and returns an error when
//! it is unavailable; the harness refuses to start instead of degrading to an
//! unsandboxed run. The only escape hatch is the explicit `--no-sandbox` flag.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context};

/// OS-level filesystem sandbox wrapping the bash tool's child process.
pub struct Sandbox {
    backend: Backend,
}

enum Backend {
    /// macOS seatbelt profile text, passed to `sandbox-exec -p`.
    #[cfg(target_os = "macos")]
    Seatbelt(String),
    /// Linux bubblewrap: read-write bind mounts (everything else stays on the
    /// read-only bind of `/`).
    #[cfg(target_os = "linux")]
    Bwrap { read_write: Vec<PathBuf> },
}

impl Sandbox {
    /// Collect the allowed paths and probe the platform backend.
    ///
    /// `cwd` is the job directory (read-write), `session_dir` the
    /// `--session-dir` (read-write), `skill_dirs` the explicit `--skill`
    /// directories (read-only; Linux covers them via the read-only `/` bind).
    pub fn new(
        cwd: &Path,
        session_dir: Option<&Path>,
        skill_dirs: &[PathBuf],
    ) -> anyhow::Result<Self> {
        let read_write = collect_read_write(cwd, session_dir)?;
        Self::build(read_write, skill_dirs)
    }

    #[cfg(target_os = "macos")]
    fn build(read_write: Vec<PathBuf>, skill_dirs: &[PathBuf]) -> anyhow::Result<Self> {
        probe_macos()?;
        let mut read_only = macos_system_read_paths();
        for dir in skill_dirs {
            read_only.push(dir.canonicalize().with_context(|| {
                format!("failed to canonicalize skill dir `{}`", dir.display())
            })?);
        }
        Ok(Self {
            backend: Backend::Seatbelt(seatbelt_profile(&read_only, &read_write)),
        })
    }

    #[cfg(target_os = "linux")]
    fn build(read_write: Vec<PathBuf>, skill_dirs: &[PathBuf]) -> anyhow::Result<Self> {
        // Skill dirs need no extra bind: the read-only `/` bind already
        // covers every read-only location.
        let _ = skill_dirs;
        probe_linux()?;
        Ok(Self {
            backend: Backend::Bwrap { read_write },
        })
    }

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    fn build(read_write: Vec<PathBuf>, skill_dirs: &[PathBuf]) -> anyhow::Result<Self> {
        let _ = (read_write, skill_dirs);
        Err(anyhow!(
            "no filesystem sandbox backend on this platform (supported: macOS seatbelt, Linux bubblewrap)"
        ))
    }

    /// Wrap an argv (e.g. `["bash", "-c", command]`) in the sandbox.
    /// Returns `(program, argv)` to spawn.
    pub fn wrap(&self, inner: &[String]) -> (String, Vec<String>) {
        match &self.backend {
            #[cfg(target_os = "macos")]
            Backend::Seatbelt(profile) => {
                let mut argv = vec!["-p".to_string(), profile.clone()];
                argv.extend(inner.iter().cloned());
                ("sandbox-exec".to_string(), argv)
            }
            #[cfg(target_os = "linux")]
            Backend::Bwrap { read_write } => ("bwrap".to_string(), bwrap_argv(read_write, inner)),
        }
    }
}

/// Read-write roots: cwd, session dir, `$TMPDIR` (canonicalized — on macOS
/// `/var/folders/...` resolves to `/private/var/folders/...`), and `/tmp`.
fn collect_read_write(cwd: &Path, session_dir: Option<&Path>) -> anyhow::Result<Vec<PathBuf>> {
    let mut paths: Vec<PathBuf> = Vec::new();
    let mut push = |path: PathBuf| {
        if !paths.contains(&path) {
            paths.push(path);
        }
    };
    push(cwd.canonicalize().with_context(|| {
        format!(
            "failed to canonicalize working directory `{}`",
            cwd.display()
        )
    })?);
    if let Some(dir) = session_dir {
        push(
            dir.canonicalize().with_context(|| {
                format!("failed to canonicalize session dir `{}`", dir.display())
            })?,
        );
    }
    push(canonical_or_raw(std::env::temp_dir()));
    push(canonical_or_raw(PathBuf::from("/tmp")));
    Ok(paths)
}

fn canonical_or_raw(path: PathBuf) -> PathBuf {
    path.canonicalize().unwrap_or(path)
}

/// System locations a process must be able to READ to execute at all
/// (binaries, dyld cache, linker config, device nodes, Homebrew prefix).
#[cfg(target_os = "macos")]
fn macos_system_read_paths() -> Vec<PathBuf> {
    [
        "/usr",
        "/bin",
        "/sbin",
        "/System",
        "/Library",
        "/private/etc",
        "/private/var/db/dyld",
        "/dev",
        "/opt/homebrew",
    ]
    .iter()
    .map(PathBuf::from)
    .filter_map(|path| path.canonicalize().ok())
    .collect()
}

/// Escape a path for embedding in a seatbelt profile string literal.
#[cfg(target_os = "macos")]
fn seatbelt_escape(path: &Path) -> String {
    path.display()
        .to_string()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
}

/// Generate the seatbelt profile.
///
/// Structure (validated empirically on macOS 15):
///
/// - `file-read-metadata` is allowed GLOBALLY: with it restricted, dyld /
///   libsystem kill the process at exec (abort/bus error) before `main`.
///   Metadata (stat) alone cannot read file contents or directory entries.
/// - `file-read-data` is restricted to the allowlist below, which must also
///   contain `(literal "/")` — the root directory is opened at process
///   startup. Read-data denial is what blocks content scanning (`readdir`
///   and `open(O_RDONLY)` outside the roots fail with EPERM).
/// - Both `literal` (the directory itself) and `subpath` (its contents) are
///   listed per root: seatbelt treats them as distinct filters.
/// - Read-write roots are also readable.
#[cfg(target_os = "macos")]
fn seatbelt_profile(read_only: &[PathBuf], read_write: &[PathBuf]) -> String {
    let mut profile = String::from("(version 1)\n(deny default)\n");
    // What bash and its children need to run at all (no filesystem effect).
    profile.push_str(
        "(allow process-exec)\n(allow process-fork)\n(allow signal)\n(allow sysctl-read)\n",
    );
    profile.push_str("(allow file-read-metadata)\n");

    let emit = |path: &Path, profile: &mut String| {
        let escaped = seatbelt_escape(path);
        profile.push_str(&format!("  (literal \"{escaped}\")\n"));
        profile.push_str(&format!("  (subpath \"{escaped}\")\n"));
    };

    profile.push_str("(allow file-read-data\n  (literal \"/\")\n");
    for path in read_only {
        emit(path, &mut profile);
    }
    for path in read_write {
        emit(path, &mut profile);
    }
    profile.push_str(")\n");

    profile.push_str("(allow file-write*\n  (subpath \"/dev\")\n");
    for path in read_write {
        emit(path, &mut profile);
    }
    profile.push_str(")\n");
    profile
}

/// `bwrap` argv: everything read-only except the read-write roots. `/tmp`
/// becomes an empty tmpfs (scratch writes stay off the host); read-write
/// binds come after it because later mounts win.
#[cfg(any(target_os = "linux", test))]
fn bwrap_argv(read_write: &[PathBuf], inner: &[String]) -> Vec<String> {
    let mut argv: Vec<String> = vec![
        "--die-with-parent".into(),
        "--ro-bind".into(),
        "/".into(),
        "/".into(),
        "--dev".into(),
        "/dev".into(),
        "--proc".into(),
        "/proc".into(),
    ];
    if read_write.iter().any(|path| path == Path::new("/tmp")) {
        argv.extend(["--tmpfs".into(), "/tmp".into()]);
    }
    for path in read_write {
        if path == Path::new("/tmp") {
            continue;
        }
        let display = path.display().to_string();
        argv.extend(["--bind".into(), display.clone(), display]);
    }
    argv.push("--".into());
    argv.extend(inner.iter().cloned());
    argv
}

#[cfg(target_os = "macos")]
fn probe_macos() -> anyhow::Result<()> {
    let status = std::process::Command::new("sandbox-exec")
        .args(["-p", "(version 1) (allow default)", "/usr/bin/true"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .context("sandbox-exec not found")?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("sandbox-exec probe failed: {status}"))
    }
}

#[cfg(target_os = "linux")]
fn probe_linux() -> anyhow::Result<()> {
    let status = std::process::Command::new("bwrap")
        .arg("--version")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .context("bwrap not found")?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("bwrap probe failed: {status}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inner() -> Vec<String> {
        vec!["bash".into(), "-c".into(), "echo hi".into()]
    }

    #[test]
    fn bwrap_argv_read_only_root_plus_write_binds() {
        let rw = vec![
            PathBuf::from("/job/dir"),
            PathBuf::from("/job/dir/session"),
            PathBuf::from("/tmp"),
        ];
        let argv = bwrap_argv(&rw, &inner());
        let joined = argv.join(" ");
        assert!(joined.contains("--die-with-parent"));
        assert!(joined.contains("--ro-bind / /"));
        assert!(
            joined.contains("--tmpfs /tmp"),
            "tmpfs /tmp missing: {joined}"
        );
        // /tmp is the tmpfs, never a bind.
        assert!(
            !joined.contains("--bind /tmp"),
            "unexpected /tmp bind: {joined}"
        );
        assert!(joined.contains("--bind /job/dir /job/dir"));
        assert!(joined.contains("--bind /job/dir/session /job/dir/session"));
        // tmpfs must precede the binds (later mounts win).
        assert!(
            argv.iter().position(|a| a == "--tmpfs").unwrap()
                < argv.iter().position(|a| a == "--bind").unwrap()
        );
        // `--` separates bwrap flags from the wrapped command.
        let sep = argv.iter().position(|a| a == "--").unwrap();
        assert_eq!(&argv[sep + 1..], inner().as_slice());
    }

    #[test]
    fn bwrap_argv_without_tmp_dir_has_no_tmpfs() {
        let argv = bwrap_argv(&[PathBuf::from("/job")], &inner());
        assert!(!argv.iter().any(|a| a == "--tmpfs"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_denies_by_default_and_lists_roots() {
        let profile = seatbelt_profile(
            &[PathBuf::from("/skill/dir")],
            &[PathBuf::from("/job/dir"), PathBuf::from("/private/tmp")],
        );
        assert!(profile.starts_with("(version 1)\n(deny default)\n"));
        assert!(profile.contains("(allow process-exec)"));
        assert!(profile.contains("(allow process-fork)"));
        // dyld/libsystem abort at exec without global metadata + a readable
        // root dir; see the seatbelt_profile doc comment.
        assert!(profile.contains("(allow file-read-metadata)"));
        assert!(profile.contains("(allow file-read-data\n  (literal \"/\")\n"));
        assert!(profile.contains("(subpath \"/skill/dir\")"));
        assert!(profile.contains("(literal \"/job/dir\")"));
        assert!(profile.contains("(subpath \"/private/tmp\")"));
        // Write allowlist must not contain the read-only skill dir.
        let write_section = profile.split("(allow file-write*").nth(1).unwrap();
        assert!(!write_section.contains("/skill/dir"));
        assert!(write_section.contains("(subpath \"/dev\")"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn seatbelt_profile_escapes_quotes_and_backslashes() {
        let profile = seatbelt_profile(&[PathBuf::from("/weird\"quo\\te")], &[]);
        assert!(profile.contains("/weird\\\"quo\\\\te"));
    }

    #[test]
    fn collect_read_write_includes_cwd_session_and_tmp() {
        let cwd = std::env::temp_dir().join("velites-sandbox-test-cwd");
        std::fs::create_dir_all(&cwd).unwrap();
        let session = cwd.join("session");
        std::fs::create_dir_all(&session).unwrap();
        let paths = collect_read_write(&cwd, Some(&session)).unwrap();
        assert!(paths.contains(&cwd.canonicalize().unwrap()));
        assert!(paths.contains(&session.canonicalize().unwrap()));
        assert!(paths.contains(&canonical_or_raw(std::env::temp_dir())));
        assert!(paths.contains(&canonical_or_raw(PathBuf::from("/tmp"))));
        std::fs::remove_dir_all(&cwd).unwrap();
    }
}
