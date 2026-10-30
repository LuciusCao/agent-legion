#[cfg(target_os = "linux")]
use super::*;

#[cfg(target_os = "linux")]
#[test]
fn launcher_is_limited_to_nonroot_nonsetuid() {
    assert!(!requires_launcher(0, false));
    assert!(!requires_launcher(1001, true));
    assert!(requires_launcher(1001, false));
}

#[cfg(target_os = "linux")]
#[test]
fn mapping_is_one_identity_line() {
    assert_eq!(mapping_line(1001), "1001 1001 1\n");
    assert_eq!(mapping_line(20), "20 20 1\n");
}

#[cfg(target_os = "linux")]
#[test]
fn launcher_argv_preserves_bwrap_policy() {
    let args = vec![
        "--unshare-pid".to_string(),
        "--".to_string(),
        "true".to_string(),
    ];
    let argv = launcher_arguments(PathBuf::from("/usr/bin/bwrap"), &args);
    assert_eq!(&argv[..3], ["sandbox", "userns-launcher", "--"]);
    assert_eq!(argv[3], "/usr/bin/bwrap");
    assert_eq!(&argv[4..], args);
}

#[cfg(target_os = "linux")]
#[test]
fn validate_bwrap_args_stops_at_payload_separator() {
    let payload = vec![
        "--unshare-pid".into(),
        "--".into(),
        "echo".into(),
        "--userns".into(),
    ];
    assert!(validate_bwrap_args(&payload).is_ok());
    let bwrap_option = vec![
        "--unshare-pid".into(),
        "--userns".into(),
        "--".into(),
        "true".into(),
    ];
    assert!(validate_bwrap_args(&bwrap_option).is_err());
}

#[cfg(target_os = "linux")]
#[test]
fn resolve_executable_returns_absolute_checked_path() {
    let dir = tempfile::tempdir().expect("tempdir");
    let binary = dir.path().join("bin").join("bwrap");
    std::fs::create_dir(binary.parent().unwrap()).unwrap();
    std::fs::write(&binary, "#!/bin/sh\n").unwrap();
    let mut permissions = std::fs::metadata(&binary).unwrap().permissions();
    use std::os::unix::fs::PermissionsExt;
    permissions.set_mode(0o755);
    std::fs::set_permissions(&binary, permissions).unwrap();
    let resolved = resolve_executable("bin/bwrap", dir.path()).expect("resolve bwrap");
    assert!(resolved.is_absolute());
    assert_eq!(resolved, binary.canonicalize().unwrap());
}

#[tokio::test]
async fn no_sandbox_runs_plain_bash_successfully() {
    let dir = tempfile::tempdir().expect("tempdir");
    let context = crate::tools::ToolContext {
        cwd: dir.path().canonicalize().unwrap(),
        cancel: crate::cancel::CancelToken::default(),
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    };
    let output = crate::tools::ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "printf plain-bash"}),
            &context,
        )
        .await;
    assert!(!output.is_error);
    let crate::events::ContentBlock::Text { text } = &output.content[0] else {
        panic!("expected text output")
    };
    assert!(text.contains("plain-bash"));
}

#[cfg(target_os = "linux")]
#[test]
fn wait_child_times_out_and_closes_socket() {
    let mut sockets = [0i32; 2];
    assert_eq!(
        unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_DGRAM, 0, sockets.as_mut_ptr()) },
        0
    );
    let (parent, child) = (sockets[0], sockets[1]);
    let pid = unsafe { libc::fork() };
    assert!(pid >= 0);
    if pid == 0 {
        unsafe { libc::close(parent) };
        std::thread::sleep(std::time::Duration::from_secs(30));
        unsafe { libc::_exit(0) }
    }
    unsafe { libc::close(child) };
    let error = wait_child(pid, parent).expect_err("sleeping child must time out");
    assert!(error.to_string().contains("timed out"));
    assert_eq!(unsafe { libc::fcntl(parent, libc::F_GETFD) }, -1);
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::EBADF)
    );
}

#[cfg(target_os = "linux")]
#[ignore = "requires unprivileged user namespaces; run explicitly in the native probe"]
#[test]
fn prepared_namespace_fd_is_live_and_closes_with_owner() {
    let fd = prepare_user_namespace().expect("prepare user namespace");
    let raw = fd.as_raw_fd();
    assert!(unsafe { libc::fcntl(raw, libc::F_GETFD) } >= 0);
    let path = format!("/proc/self/fd/{raw}");
    assert!(std::fs::read_link(path).is_ok());
    drop(fd);
    assert_eq!(unsafe { libc::fcntl(raw, libc::F_GETFD) }, -1);
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::EBADF)
    );
}
