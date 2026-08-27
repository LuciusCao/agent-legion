//! Unit tests for the OS filesystem sandbox: bwrap/seatbelt argv and
//! profile structure, resolv.conf escape binds, ancestor list-only chains,
//! read-write collection, and python3 root probing. Split from ``mod.rs``
//! for the file size budget (#202).

use std::path::{Path, PathBuf};

use super::*;

#[cfg(target_os = "macos")]
use super::macos::seatbelt_escape;

fn inner() -> Vec<String> {
    vec!["bash".into(), "-c".into(), "echo hi".into()]
}

#[test]
fn bwrap_argv_selective_binds_plus_write_binds() {
    let rw = vec![
        PathBuf::from("/job/dir"),
        PathBuf::from("/job/dir/session"),
        PathBuf::from("/tmp"),
    ];
    let ro = vec![PathBuf::from("/skill/dir")];
    let argv = bwrap_argv(&ro, &rw, &inner());
    let joined = argv.join(" ");
    assert!(joined.contains("--die-with-parent"));
    // No blanket root bind: reads are limited to the selective binds.
    assert!(
        !joined.contains("--ro-bind / /"),
        "blanket root bind leaked: {joined}"
    );
    // System roots present on every supported host (the argv builder
    // skips missing ones, so only assert roots that exist everywhere).
    assert!(joined.contains("--ro-bind /usr /usr"));
    assert!(joined.contains("--ro-bind /etc /etc"));
    // Read-only roots are bound read-only, never read-write.
    assert!(joined.contains("--ro-bind /skill/dir /skill/dir"));
    assert!(!joined.contains("--bind /skill/dir"));
    // The bash tool keeps its shared network and pid namespaces.
    assert!(!joined.contains("--unshare-net"));
    assert!(!joined.contains("--unshare-pid"));
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
    let argv = bwrap_argv(&[], &[PathBuf::from("/job")], &inner());
    assert!(!argv.iter().any(|a| a == "--tmpfs"));
}

#[test]
fn bwrap_wrap_argv_selective_binds_and_private_namespaces() {
    let rw = vec![PathBuf::from("/job/dir"), PathBuf::from("/tmp")];
    let ro = vec![PathBuf::from("/repo/server"), PathBuf::from("/opt/venv")];
    let argv = bwrap_wrap_argv(&ro, &rw, &inner(), false);
    let joined = argv.join(" ");
    assert!(joined.contains("--unshare-pid"));
    assert!(joined.contains("--unshare-net"));
    // No blanket root bind in wrap mode.
    assert!(
        !joined.contains("--ro-bind / /"),
        "blanket root bind leaked: {joined}"
    );
    assert!(joined.contains("--ro-bind /usr /usr"));
    assert!(joined.contains("--ro-bind /repo/server /repo/server"));
    assert!(joined.contains("--ro-bind /opt/venv /opt/venv"));
    assert!(joined.contains("--bind /job/dir /job/dir"));
    assert!(joined.contains("--tmpfs /tmp"));
    // Network opt-in drops only the net unshare; pid stays private.
    let allowed = bwrap_wrap_argv(&ro, &rw, &inner(), true).join(" ");
    assert!(!allowed.contains("--unshare-net"));
    assert!(allowed.contains("--unshare-pid"));
}

#[cfg(unix)]
#[test]
fn resolv_conf_target_outside_flags_symlink_leaving_etc() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir(dir.path().join("etc")).unwrap();
    let etc = dir.path().join("etc").canonicalize().unwrap();
    // A real file inside etc is covered by the /etc ro-bind: no extra bind.
    std::fs::write(etc.join("resolv.conf"), "nameserver 1.1.1.1").unwrap();
    assert_eq!(
        resolv_conf_target_outside(&etc.join("resolv.conf"), &etc),
        None
    );
    // systemd-resolved style: a symlink escaping etc → bind the target.
    let run = dir.path().join("run/systemd/resolve");
    std::fs::create_dir_all(&run).unwrap();
    let target = run.join("resolv.conf");
    std::fs::write(&target, "nameserver 127.0.0.53").unwrap();
    std::os::unix::fs::symlink(&target, etc.join("resolv-link")).unwrap();
    assert_eq!(
        resolv_conf_target_outside(&etc.join("resolv-link"), &etc),
        Some(target.canonicalize().unwrap())
    );
    // A missing file yields no bind.
    assert_eq!(resolv_conf_target_outside(&etc.join("missing"), &etc), None);
}

#[test]
fn bwrap_argvs_bind_resolv_conf_target_when_it_leaves_etc() {
    // Host-adaptive: only hosts whose /etc/resolv.conf resolves outside
    // /etc (systemd-resolved; macOS' /private/etc in test builds) get
    // the extra bind. Both the bash tool and wrap argv must agree.
    let expected = resolv_conf_read_root();
    for joined in [
        bwrap_argv(&[], &[PathBuf::from("/job")], &inner()).join(" "),
        bwrap_wrap_argv(&[], &[PathBuf::from("/job")], &inner(), false).join(" "),
    ] {
        match &expected {
            Some(target) => {
                let display = target.display().to_string();
                assert!(
                    joined.contains(&format!("--ro-bind-try {display} {display}")),
                    "resolv.conf bind missing: {joined}"
                );
            }
            None => assert!(
                !joined.contains("--ro-bind-try"),
                "unexpected resolv.conf bind: {joined}"
            ),
        }
    }
}

#[test]
fn bwrap_argv_opts_unshares_network_only_when_requested() {
    let shared = bwrap_argv_opts(&[], &[PathBuf::from("/job")], &inner(), false);
    assert!(!shared.iter().any(|a| a == "--unshare-net"));
    let isolated = bwrap_argv_opts(&[], &[PathBuf::from("/job")], &inner(), true);
    assert!(isolated.iter().any(|a| a == "--unshare-net"));
    // Network isolation lands before the first read-only bind.
    let unshare = isolated.iter().position(|a| a == "--unshare-net").unwrap();
    let ro_bind = isolated.iter().position(|a| a == "--ro-bind").unwrap();
    assert!(unshare < ro_bind);
}

#[cfg(target_os = "macos")]
#[test]
fn seatbelt_profile_ext_network_opt_in() {
    let denied = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], false, false);
    assert!(!denied.contains("network-outbound"));
    let allowed = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], true, false);
    assert!(allowed.contains("(allow network-outbound)"));
    assert!(allowed.contains("(allow network-inbound)"));
    // The base policy stays identical: deny default with the write root.
    assert!(allowed.starts_with("(version 1)\n(deny default)\n"));
    assert!(allowed.contains("(subpath \"/job\")"));
}

#[cfg(target_os = "macos")]
#[test]
fn seatbelt_wrap_profile_confines_signals_to_self() {
    let profile = seatbelt_profile_opts(&[], &[PathBuf::from("/job")], &[], false, true);
    assert!(profile.contains("(allow signal (target self))"));
    assert!(!profile.contains("(allow signal)\n"));
}

#[cfg(target_os = "macos")]
#[test]
fn seatbelt_profile_denies_by_default_and_lists_roots() {
    let profile = seatbelt_profile_opts(
        &[PathBuf::from("/skill/dir")],
        &[PathBuf::from("/job/dir"), PathBuf::from("/private/tmp")],
        &[],
        false,
        false,
    );
    // Bash policy keeps the global signal allow; wrap mode narrows it.
    assert!(profile.contains("(allow signal)"));
    assert!(!profile.contains("(target self)"));
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
    let profile =
        seatbelt_profile_opts(&[PathBuf::from("/weird\"quo\\te")], &[], &[], false, false);
    assert!(profile.contains("/weird\\\"quo\\\\te"));
}

#[test]
fn ancestor_list_only_grants_chain_excluding_root_and_slash() {
    let ro = vec![PathBuf::from("/a/b/skill")];
    let rw = vec![PathBuf::from("/a/b/job"), PathBuf::from("/a/b/session/sub")];
    let mut out = Vec::new();
    for root in ro.iter().chain(rw.iter()) {
        ancestor_list_only(root, &ro, &rw, &mut out);
    }
    // /a/b is an ancestor of every root; /a/b/session is not itself a
    // whitelisted root, so it stays list-only. "/" never appears.
    assert_eq!(
        out,
        vec![
            PathBuf::from("/a/b"),
            PathBuf::from("/a"),
            PathBuf::from("/a/b/session"),
        ]
    );
    // Roots themselves are never downgraded to list-only.
    assert!(!out.contains(&PathBuf::from("/a/b/skill")));
    assert!(!out.contains(&PathBuf::from("/a/b/job")));
    // A root directly under "/" contributes nothing.
    let mut out = Vec::new();
    ancestor_list_only(Path::new("/tmp"), &[], &[PathBuf::from("/tmp")], &mut out);
    assert!(out.is_empty());
}

#[cfg(target_os = "macos")]
#[test]
fn seatbelt_profile_bash_tool_parents_are_literal_only() {
    let ro = vec![PathBuf::from("/a/b/skill")];
    let rw = vec![PathBuf::from("/a/b/job")];
    let mut list_only = Vec::new();
    for root in ro.iter().chain(rw.iter()) {
        ancestor_list_only(root, &ro, &rw, &mut list_only);
    }
    let profile = seatbelt_profile_opts(&ro, &rw, &list_only, false, false);
    // Ancestors get a bare literal (readdir yes, file contents no)…
    assert!(profile.contains("(literal \"/a/b\")\n"));
    assert!(profile.contains("(literal \"/a\")\n"));
    // …never a subpath grant — "/a/b" appearing in "(subpath "/a/b/job")"
    // is a different string, so a plain contains-check is exact here.
    assert!(!profile.contains("(subpath \"/a/b\")\n"));
    assert!(!profile.contains("(subpath \"/a\")\n"));
    // List-only grants never reach the write allowlist.
    let write_section = profile.split("(allow file-write*").nth(1).unwrap();
    assert!(!write_section.contains("/a/b\""));
    assert!(!write_section.contains("/a\""));
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

// --- python3 read-root probe (design §8: python3 must run inside the
// sandbox; uv-style interpreters load libpython via @rpath from the
// install prefix, and venv pythons need pyvenv.cfg readable — both go on
// the read-only whitelist). ---

/// A fake uv-style install: `<tmp>/uv/python/cpython-3.13/bin/python3.13`
/// (executable), plus a `.venv/bin/python3` symlink pointing at it.
fn fake_uv_python() -> (tempfile::TempDir, PathBuf, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let prefix = dir
        .path()
        .join("uv/python/cpython-3.13.13-macos-aarch64-none");
    let bin = prefix.join("bin");
    std::fs::create_dir_all(&bin).unwrap();
    let interpreter = bin.join("python3.13");
    std::fs::write(&interpreter, "fake interpreter").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&interpreter, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    (dir, prefix, interpreter)
}

#[test]
fn python_prefix_from_canonical_computes_install_prefix() {
    let (_dir, prefix, interpreter) = fake_uv_python();
    let canonical = interpreter.canonicalize().unwrap();
    assert_eq!(
        python_prefix_from_canonical(&canonical),
        Some(prefix.canonicalize().unwrap())
    );
}

#[test]
fn python_prefix_from_canonical_rejects_system_and_bogus_paths() {
    // /usr/bin/python3 → prefix /usr: no "python" in the prefix path,
    // and /usr is already covered by the system read list — skip.
    assert_eq!(
        python_prefix_from_canonical(Path::new("/usr/bin/python3")),
        None
    );
    // Nonexistent prefix → skip (never whitelist a phantom path).
    assert_eq!(
        python_prefix_from_canonical(Path::new("/definitely/not/here/python/bin/python3")),
        None
    );
}

#[test]
fn find_executable_on_path_skips_non_executables() {
    let (dir, _prefix, interpreter) = fake_uv_python();
    // Earlier PATH entry has a NON-executable python3 — must be skipped.
    let earlier = dir.path().join("earlier");
    std::fs::create_dir(&earlier).unwrap();
    std::fs::write(earlier.join("python3"), "not executable").unwrap();
    // .venv-style symlink to the real interpreter; returned UNRESOLVED
    // (the caller needs the symlink path for venv detection).
    let venv_bin = dir.path().join("venv/bin");
    std::fs::create_dir_all(&venv_bin).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&interpreter, venv_bin.join("python3")).unwrap();

    let path_var =
        std::ffi::OsString::from(format!("{}:{}", earlier.display(), venv_bin.display()));
    let found =
        find_executable_on_path(&path_var, "python3").expect("executable python3 must be found");
    assert_eq!(found, venv_bin.join("python3"));
}

#[cfg(unix)]
#[test]
fn python_read_roots_covers_venv_root_and_base_prefix() {
    let (dir, prefix, interpreter) = fake_uv_python();
    // A venv whose bin/python3 symlinks to the uv interpreter.
    let venv = dir.path().join("project/.venv");
    std::fs::create_dir_all(venv.join("bin")).unwrap();
    std::os::unix::fs::symlink(&interpreter, venv.join("bin/python3")).unwrap();
    std::fs::write(
        venv.join("pyvenv.cfg"),
        format!("home = {}\n", prefix.join("bin").display()),
    )
    .unwrap();

    let path_var = std::ffi::OsString::from(venv.join("bin").to_string_lossy().into_owned());
    let roots = python_read_roots_from_path(&path_var);
    assert_eq!(
        roots,
        vec![venv.canonicalize().unwrap(), prefix.canonicalize().unwrap(),]
    );
}

#[cfg(unix)]
#[test]
fn python_read_roots_skips_venv_without_pyvenv_cfg() {
    let (dir, prefix, interpreter) = fake_uv_python();
    // A bare bin dir (no pyvenv.cfg) is NOT a venv: only the base
    // install prefix is whitelisted.
    let bare_bin = dir.path().join("tools/bin");
    std::fs::create_dir_all(&bare_bin).unwrap();
    std::os::unix::fs::symlink(&interpreter, bare_bin.join("python3")).unwrap();

    let path_var = std::ffi::OsString::from(bare_bin.to_string_lossy().into_owned());
    let roots = python_read_roots_from_path(&path_var);
    assert_eq!(roots, vec![prefix.canonicalize().unwrap()]);
}

#[test]
fn python_read_roots_empty_without_python3_on_path() {
    let dir = tempfile::tempdir().unwrap();
    let path_var = std::ffi::OsString::from(dir.path().to_string_lossy().into_owned());
    assert!(python_read_roots_from_path(&path_var).is_empty());
}

#[cfg(target_os = "macos")]
#[cfg(unix)]
#[test]
fn seatbelt_profile_includes_python_roots_read_only() {
    let (dir, prefix, interpreter) = fake_uv_python();
    let venv = dir.path().join("project/.venv");
    std::fs::create_dir_all(venv.join("bin")).unwrap();
    std::os::unix::fs::symlink(&interpreter, venv.join("bin/python3")).unwrap();
    std::fs::write(
        venv.join("pyvenv.cfg"),
        format!("home = {}\n", prefix.join("bin").display()),
    )
    .unwrap();
    let path_var = std::ffi::OsString::from(venv.join("bin").to_string_lossy().into_owned());
    let roots = python_read_roots_from_path(&path_var);

    let profile = seatbelt_profile_opts(&roots, &[], &[], false, false);
    for root in &roots {
        let escaped = seatbelt_escape(root);
        assert!(profile.contains(&format!("(literal \"{escaped}\")")));
        assert!(profile.contains(&format!("(subpath \"{escaped}\")")));
        // Read-only: the write section must not mention the root.
        let write_section = profile.split("(allow file-write*").nth(1).unwrap();
        assert!(!write_section.contains(escaped.as_str()));
    }
}

#[test]
fn python_read_roots_on_real_path_respects_guards() {
    // Environment-dependent, so only assert the invariant: every probed
    // root is an existing directory.
    for root in python_read_roots() {
        assert!(root.is_dir(), "root must exist: {}", root.display());
    }
}
